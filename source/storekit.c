// StoreKit emulado: la App Store de estas versiones ya no existe, así que toda compra se completa sin cobrar
// (como el bypass de compras de Rayman Jungle Run). Lo no consumible (PORT_IAP_UNLOCK) además se entrega como
// "restaurado" al registrarse el observer, para que el juego quede desbloqueado sin tocar la tienda.
// Las respuestas van por storekit_tick() en el run loop principal: en iOS son asíncronas y en el hilo principal.
#include <stdio.h>
#include <string.h>

#include "foundation.h"
#include "hle.h"
#include "log.h"

enum { SK_PURCHASED = 1, SK_RESTORED = 3 };

typedef struct {
    objc_class *isa;
    id product_id; // NSString
    int quantity;
} sk_payment;
typedef struct {
    objc_class *isa;
    id payment, tid, date, receipt;
    int state;
} sk_transaction;
typedef struct {
    objc_class *isa;
    id ids, delegate; // NSSet de NSString
} sk_request;
typedef struct {
    objc_class *isa;
    id products;
} sk_response;
typedef struct {
    objc_class *isa;
    id product_id;
} sk_product;

static objc_class *c_SKPayment, *c_SKMutablePayment, *c_SKPaymentTransaction, *c_SKProductsResponse, *c_SKProduct,
    *c_SKPaymentQueue;
static id queue, observers; // observers: NSMutableArray

// pendientes para storekit_tick (hilo principal)
#define MAX_PENDING 16
static struct {
    int kind; // 1 = transacción, 2 = respuesta de productos, 3 = fin de restaurar
    id obj;
} pending[MAX_PENDING];
static int npending, unlock_sent;

static void push(int kind, id obj) {
    if (npending < MAX_PENDING)
        pending[npending].kind = kind, pending[npending++].obj = obj; // obj +1
}

static id make_transaction(const char *pid, int state) {
    static int seq;
    sk_payment *p = (sk_payment *)objc_alloc(c_SKPayment);
    p->product_id = ns_string_new(pid);
    p->quantity = 1;
    sk_transaction *t = (sk_transaction *)objc_alloc(c_SKPaymentTransaction);
    t->payment = (id)p;
    t->state = state;
    char buf[32];
    snprintf(buf, sizeof(buf), "%d", 1000000 + ++seq);
    t->tid = ns_string_new(buf);
    t->date = objc_retain(MSG0(objc_getClass("NSDate"), "date"));
    t->receipt = objc_retain(MSG2(objc_getClass("NSData"), "dataWithBytes:length:", "{}", 2));
    l_info("StoreKit: transacción %s -> %s", pid, state == SK_RESTORED ? "restaurada" : "comprada");
    return (id)t;
}

// ---- SKPayment / SKMutablePayment
static id pay_withId(objc_class *cls, SEL _c, id pid) {
    sk_payment *p = (sk_payment *)objc_alloc(c_SKPayment);
    p->product_id = objc_retain(pid);
    p->quantity = 1;
    return objc_autorelease((id)p);
}
static id pay_withProduct(objc_class *cls, SEL _c, id product) {
    return pay_withId(cls, _c, product ? ((sk_product *)product)->product_id : NULL);
}
static id pay_productId(sk_payment *self, SEL _c) { return self->product_id; }
static int pay_quantity(sk_payment *self, SEL _c) { return self->quantity; }
static void pay_setQuantity(sk_payment *self, SEL _c, int q) { self->quantity = q; }
static void pay_setProductId(sk_payment *self, SEL _c, id pid) {
    objc_release(self->product_id);
    self->product_id = objc_retain(pid);
}
static const nmethod pay_c[] = {NM("paymentWithProductIdentifier:", pay_withId), NM("paymentWithProduct:", pay_withProduct),
                                {0}};
static const nmethod pay_i[] = {NM("productIdentifier", pay_productId), NM("quantity", pay_quantity),
                                NM("setQuantity:", pay_setQuantity), NM("setProductIdentifier:", pay_setProductId), {0}};

// ---- SKPaymentTransaction
static id tr_payment(sk_transaction *self, SEL _c) { return self->payment; }
static int tr_state(sk_transaction *self, SEL _c) { return self->state; }
static id tr_tid(sk_transaction *self, SEL _c) { return self->tid; }
static id tr_date(sk_transaction *self, SEL _c) { return self->date; }
static id tr_receipt(sk_transaction *self, SEL _c) { return self->receipt; }
static id tr_original(sk_transaction *self, SEL _c) { return self->state == SK_RESTORED ? (id)self : NULL; }
static id tr_nil(id self, SEL _c) { return NULL; }
static const nmethod tr_i[] = {NM("payment", tr_payment), NM("transactionState", tr_state),
                               NM("transactionIdentifier", tr_tid), NM("transactionDate", tr_date),
                               NM("transactionReceipt", tr_receipt), NM("originalTransaction", tr_original),
                               NM("error", tr_nil), {0}};

// ---- SKProduct / SKProductsResponse / SKProductsRequest
static id pr_id(sk_product *self, SEL _c) { return self->product_id; }
static id pr_title(sk_product *self, SEL _c) { return self->product_id; }
static id pr_price(id self, SEL _c) { return MSG1(objc_getClass("NSNumber"), "numberWithInt:", 0); }
static id pr_locale(id self, SEL _c) { return MSG0(objc_getClass("NSLocale"), "currentLocale"); }
static const nmethod pr_i[] = {NM("productIdentifier", pr_id), NM("localizedTitle", pr_title),
                               NM("localizedDescription", pr_title), NM("price", pr_price),
                               NM("priceLocale", pr_locale), {0}};
static id rs_products(sk_response *self, SEL _c) { return self->products; }
static id rs_invalid(id self, SEL _c) { return objc_autorelease(ns_array_new()); }
static const nmethod rs_i[] = {NM("products", rs_products), NM("invalidProductIdentifiers", rs_invalid), {0}};

static id rq_init(sk_request *self, SEL _c, id ids) {
    self->ids = objc_retain(ids);
    return (id)self;
}
static void rq_setDelegate(sk_request *self, SEL _c, id d) { self->delegate = d; }
static id rq_delegate(sk_request *self, SEL _c) { return self->delegate; }
static void rq_start(sk_request *self, SEL _c) { push(2, objc_retain((id)self)); }
static void rq_noop(id self, SEL _c) {}
static const nmethod rq_i[] = {NM("initWithProductIdentifiers:", rq_init), NM("setDelegate:", rq_setDelegate),
                               NM("delegate", rq_delegate), NM("start", rq_start), NM("cancel", rq_noop), {0}};

// ---- SKPaymentQueue
static id q_default(objc_class *cls, SEL _c) {
    if (!queue)
        queue = objc_alloc(c_SKPaymentQueue);
    return queue;
}
static int q_canMake(id self, SEL _c) { return 1; }
static void q_addObserver(id self, SEL _c, id o) {
    if (!observers)
        observers = ns_array_new();
    ns_array_add(observers, o);
#ifdef PORT_IAP_UNLOCK
    if (!unlock_sent) { // compra no consumible ya hecha (como al reinstalar en iOS: llega sola al observer)
        unlock_sent = 1;
        push(1, make_transaction(PORT_IAP_UNLOCK, SK_RESTORED));
    }
#endif
}
static void q_removeObserver(id self, SEL _c, id o) { MSG1(observers, "removeObject:", o); }
static void q_addPayment(id self, SEL _c, id payment) {
    const char *pid = ns_cstr(((sk_payment *)payment)->product_id);
    l_info("StoreKit: addPayment %s", pid);
    push(1, make_transaction(pid, SK_PURCHASED));
}
static void q_restore(id self, SEL _c) {
#ifdef PORT_IAP_UNLOCK
    push(1, make_transaction(PORT_IAP_UNLOCK, SK_RESTORED));
#endif
    push(3, NULL);
}
static id q_transactions(id self, SEL _c) { return objc_autorelease(ns_array_new()); }
static const nmethod q_c[] = {NM("defaultQueue", q_default), NM("canMakePayments", q_canMake), {0}};
static const nmethod q_i[] = {NM("addTransactionObserver:", q_addObserver), NM("removeTransactionObserver:", q_removeObserver),
                              NM("addPayment:", q_addPayment), NM("finishTransaction:", rq_noop),
                              NM("restoreCompletedTransactions", q_restore), NM("transactions", q_transactions), {0}};

static void notify(const char *sel, id arg) {
    for (int i = 0; observers && i < ns_array_count(observers); i++) {
        id o = ns_array_get(observers, i);
        if (objc_responds(o, sel_registerName(sel)))
            MSG2(o, sel, queue, arg);
    }
}

// Run loop principal: entrega lo pendiente
void storekit_tick(void) {
    for (int i = 0; i < npending; i++) {
        id obj = pending[i].obj;
        if (pending[i].kind == 1) {
            id arr = ns_array_new();
            ns_array_add(arr, obj);
            notify("paymentQueue:updatedTransactions:", arr);
            objc_release(arr);
        } else if (pending[i].kind == 2) {
            sk_request *rq = (sk_request *)obj;
            sk_response *rs = (sk_response *)objc_alloc(c_SKProductsResponse);
            rs->products = ns_array_new();
            id all = MSG0(rq->ids, "allObjects");
            for (int k = 0; k < ns_array_count(all); k++) {
                sk_product *p = (sk_product *)objc_alloc(c_SKProduct);
                p->product_id = objc_retain(ns_array_get(all, k));
                ns_array_add(rs->products, (id)p);
                objc_release((id)p);
            }
            l_info("StoreKit: %d productos para el delegate", ns_array_count(rs->products));
            if (rq->delegate) {
                MSG2(rq->delegate, "productsRequest:didReceiveResponse:", obj, rs);
                if (objc_responds(rq->delegate, sel_registerName("requestDidFinish:")))
                    MSG1(rq->delegate, "requestDidFinish:", obj);
            }
            objc_release((id)rs);
        } else {
            for (int k = 0; observers && k < ns_array_count(observers); k++) {
                id o = ns_array_get(observers, k);
                if (objc_responds(o, sel_registerName("paymentQueueRestoreCompletedTransactionsFinished:")))
                    MSG1(o, "paymentQueueRestoreCompletedTransactionsFinished:", queue);
            }
        }
        if (obj)
            objc_release(obj);
    }
    npending = 0;
}

void storekit_init(void) {
    c_SKPayment = objc_define("SKPayment", "NSObject", sizeof(sk_payment), pay_i, pay_c);
    c_SKMutablePayment = objc_define("SKMutablePayment", "SKPayment", sizeof(sk_payment), NULL, NULL);
    c_SKPaymentTransaction = objc_define("SKPaymentTransaction", "NSObject", sizeof(sk_transaction), tr_i, NULL);
    c_SKProduct = objc_define("SKProduct", "NSObject", sizeof(sk_product), pr_i, NULL);
    c_SKProductsResponse = objc_define("SKProductsResponse", "NSObject", sizeof(sk_response), rs_i, NULL);
    objc_define("SKProductsRequest", "NSObject", sizeof(sk_request), rq_i, NULL);
    c_SKPaymentQueue = objc_define("SKPaymentQueue", "NSObject", 4, q_i, q_c);
}
