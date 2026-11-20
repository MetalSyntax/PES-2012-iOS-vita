// Parches de PES 2012 (iOS, Marmalade enlazado en el Mach-O). Solo lo que el análisis estático
// justifica; el resto de imports sin implementar sale en el log como "unimpl".
#include <psp2/kernel/threadmgr.h>
#include <stdio.h>
#include <malloc.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "imports.h"
#include "log.h"
#include "macho.h"
#include "objc.h"
#include "patch.h"

// zlib (z_stream = 56 bytes en los dos)
void inflate(void), inflateEnd(void), inflateInit2_(void), inflateInit_(void), tzset(void);

// s3eThreadCreate_platform le da a pthread un stack propio del heap: en la Vita el kernel valida SP
// contra el stack real del hilo (stop 0x10006, Bug #18 del port Android), así que se ignora la
// dirección y solo se respeta el tamaño. d_attr de darwin.c = {sig, stack, detached, prio}.
static int p_pthread_attr_setstack(uint32_t *a, void *addr, uint32_t size) {
    a[1] = size;
    return 0;
}
// statvfs de Darwin 32 bits: 11 palabras (bsize, frsize, blocks, bfree, bavail, files, ffree, favail, fsid, flag, namemax)
static int p_statvfs(const char *path, uint32_t *st) {
    memset(st, 0, 11 * 4);
    st[0] = st[1] = 4096;
    st[2] = 1 << 20;
    st[3] = st[4] = 1 << 18; // 1 GB libre
    st[10] = 255;
    return 0;
}

// Diagnóstico: Marmalade aborta con "Not enough memory to begin loading s3e file" cuando falla
// s3eMallocOS_platform (= malloc). Se registra el tamaño pedido y el estado del heap de newlib.
static void log_oom(const char *fn, size_t n) {
    struct mallinfo mi = mallinfo();
    l_error("%s(%u) = NULL (heap newlib: arena %u, en uso %u, libre %u)", fn, (unsigned)n, (unsigned)mi.arena,
            (unsigned)mi.uordblks, (unsigned)mi.fordblks);
    // backtrace aproximado: palabras de la pila que apuntan a __TEXT del juego (restar MACHO_SLIDE)
    uint32_t *sp = (uint32_t *)__builtin_frame_address(0), lo, size;
    if (macho_section("__TEXT", "__text", &lo, &size) < 0)
        return;
    for (int i = 0, k = 0; i < 256 && k < 6; i++)
        if (sp[i] >= lo && sp[i] < lo + size)
            l_error("  pila[%d] = 0x%x", i, (unsigned)(sp[i] - MACHO_SLIDE)), k++;
}
static void *p_malloc(size_t n) {
    void *r = malloc(n);
    if (!r && n)
        log_oom("malloc", n);
    if (n == 0x25B4)  // diagnóstico PES 2012: objeto de 0x8b788 que termina con this = NULL
        l_info("malloc(0x25b4) = %p", r);
    return r;
}
static void *p_calloc(size_t a, size_t b) {
    void *r = calloc(a, b);
    if (!r && a && b)
        log_oom("calloc", a * b);
    return r;
}
static void *p_realloc(void *o, size_t n) {
    void *r = realloc(o, n);
    if (!r && n)
        log_oom("realloc", n);
    return r;
}

const import_entry patch_imports[] = {
    F(malloc, p_malloc), F(calloc, p_calloc), F(realloc, p_realloc),
    D(inflate), D(inflateEnd), D(inflateInit2_), D(inflateInit_), D(tzset),
    F(pthread_attr_setstack, p_pthread_attr_setstack), F(statvfs, p_statvfs),
};
TABLE_END(patch)

// _s3eStackSwitchReadConfig (0x4113c) fuerza el byte SysStackSwitch = 1 (mov r3,#1; strb r3,[r2,#0xc]),
// sin leer el icf. Con 1, cada llamada s3e cambia SP a un "loader stack" del heap y una syscall
// bloqueante ahí detiene el hilo en la Vita (mismo caso que Bug #18 de ../Incompletes/PES-2012-vita).
// Con 0, _s3eStackSwitchIsDisabled devuelve 1 y todas las llamadas van por el camino directo.
static void disable_sys_stack_switch(void) {
    uintptr_t f = macho_sym("_s3eStackSwitchReadConfig");
    uint32_t vm = f ? (uint32_t)(f - MACHO_SLIDE) + 0x30 : 0x4116c, mov_r3_0 = 0xe3a03000;
    if (macho_read_text(vm) != 0xe3a03001 || macho_read_text(vm + 4) != 0xe5c2300c) {
        l_error("SysStackSwitch patch: instrucción inesperada en 0x%x (0x%08x), no se toca", vm, macho_read_text(vm));
        return;
    }
    macho_patch_text(vm, &mov_r3_0, 4);
    l_info("SysStackSwitch forced off (0x%x)", vm);
}

// El app.icf embebido pide SysGlesVersion=2 en iPhone (1 en iPad2 y Android). _s3eGLInit_platform
// (0x4b57c) hace [[EAGLContext alloc] initWithAPI:2] y, si da nil, cae a initWithAPI:1. El runtime no
// exporta GLES2: devolver nil para ES2 deja a IwGx en el camino ES1, el mismo que usa el port Android.
static id (*ea_init)(id, SEL, int);
static id p_initWithAPI(id self, SEL c, int api) {
    if (api != 1) {
        l_info("EAGLContext initWithAPI:%d -> nil (forzado GLES1)", api);
        return NULL;
    }
    return ea_init(self, c, api);
}
static void force_gles1(void) {
    // vtable de una clase nativa = copia en heap con SEL internados (objc.c intern_table): se edita en el lugar.
    objc_class *c = objc_getClass("EAGLContext");
    SEL s = sel_registerName("initWithAPI:");
    for (nmethod *m = c ? c->vtable : NULL; m && m->sel; m++)
        if (m->sel == s)
            ea_init = m->imp, m->imp = (void *)p_initWithAPI;
    if (!ea_init)
        l_error("patch: EAGLContext sin initWithAPI: nativo, GLES1 no forzado");
}

// SWP/SWPB (ARMv6) es Undefined Instruction en la Vita (Cortex-A9, SCTLR.SW=0). Marmalade la usa en
// _s3eGlobalLock*_asm_arm y en los trampolines de stack switch (0x3ab88..). Cada SWP pasa a un B a un
// stub equivalente con LDREX/STREX que preserva los temporales y los flags.
static void fix_swp(void) {
    uint32_t lo, size, n = 0;
    if (macho_section("__TEXT", "__text", &lo, &size) < 0)
        return;
    lo -= MACHO_SLIDE;
    for (uint32_t vm = lo; vm < lo + size; vm += 4) {
        uint32_t w = macho_read_text(vm);
        if ((w & 0x0FB00FF0) != 0x01000090)
            continue;
        uint32_t rn = (w >> 16) & 15, rd = (w >> 12) & 15, rm = w & 15, byte = w & (1 << 22);
        if (w >> 28 != 0xE || rn == 13 || rn == 15 || rd == 13 || rd == 15 || rm == 13 || rm == 15) {
            l_error("SWP no soportado en 0x%x (0x%08x)", vm, w);
            continue;
        }
        uint32_t t[4], k = 0, mask = 0;
        for (uint32_t r = 0; k < 4; r++)
            if (r != rn && r != rd && r != rm)
                mask |= 1u << (t[k++] = r);
        uint32_t c[10] = {
            0xE92D0000 | mask,                                                        // push {t}
            0xE10F0000 | t[3] << 12,                                                  // mrs t3, cpsr
            0xE1A00000 | t[1] << 12 | rm,                                             // mov t1, rm
            (byte ? 0xE1D00F9F : 0xE1900F9F) | rn << 16 | t[0] << 12,                 // 1: ldrex(b) t0, [rn]
            (byte ? 0xE1C00F90 : 0xE1800F90) | rn << 16 | t[2] << 12 | t[1],          // strex(b) t2, t1, [rn]
            0xE3500000 | t[2] << 16,                                                  // cmp t2, #0
            0x1AFFFFFB,                                                               // bne 1b
            0xE128F000 | t[3],                                                        // msr cpsr_f, t3
            0xE1A00000 | rd << 12 | t[0],                                             // mov rd, t0
            0xE8BD0000 | mask,                                                        // pop {t}
        };                                                                            // + b vm+4
        uint32_t stub = macho_code(c, 10), here = vm + MACHO_SLIDE;
        uint32_t back = 0xEA000000 | (((here + 4 - (stub + 40 + 8)) >> 2) & 0xFFFFFF);
        macho_code(&back, 1);
        uint32_t jump = 0xEA000000 | (((stub - (here + 8)) >> 2) & 0xFFFFFF);
        macho_patch_text(vm, &jump, 4);
        n++;
    }
    l_info("SWP -> LDREX/STREX: %u sitios", n);
}

// Los datos cifrados de la app (__S3E_DATA) ya vienen descifrados de tools/ios_prep.py (s3e_decrypt): la clave
// es un hash del código en direcciones absolutas (dladdr + __text..__symbolstub1), imposible aquí.
static void p_s3eDecodeData(uint32_t key) {}

// Hook que conserva la original: copia las 2 primeras instrucciones ARM (deben ser independientes de la
// posición: push/add r7) a un trampolín + salto al resto. ponytail: solo ARM y sin chequear PC-relativas.
static uintptr_t hook_keep(const char *name, void *dst) {
    uintptr_t f = macho_sym(name);
    if (!f || (f & 1))
        return 0;
    uint32_t vm = f - MACHO_SLIDE, c[4] = {macho_read_text(vm), macho_read_text(vm + 4), 0xE51FF004, f + 8};
    uintptr_t orig = macho_code(c, 4); // ins0; ins1; ldr pc, [pc, #-4]; .word f+8
    macho_hook(name, dst);
    return orig;
}

// Por dirección (funciones ARM sin símbolo, p. ej. el SQLite de la app)
static uintptr_t hook_keep_arm(uint32_t vm, void *dst) {
    uint32_t c[4] = {macho_read_text(vm), macho_read_text(vm + 4), 0xE51FF004, vm + 8 + MACHO_SLIDE};
    uintptr_t orig = macho_code(c, 4);
    macho_hook_addr(vm, dst);
    return orig;
}

// diagnóstico PES 2012: PRAGMA integrity_check del save no devuelve "ok" (la app recopia el save abierto)
static uintptr_t sq_step_orig, sq_text_orig;
static uint32_t p_sqlite3_step(uint32_t st, uint32_t a1, uint32_t a2, uint32_t a3) {
    uint32_t r = call_guest((void *)sq_step_orig, st, a1, a2, a3);
    if (r != 100 && r != 101)
    {
        l_error("sqlite3_step(%08x) = %u", st, r);
        // diagnóstico #096: cadenas que cuelgan del Vdbe (zSql, zErrMsg) y de su sqlite3* (primer campo)
        for (int pass = 0; st && pass < 2; pass++) {
            uint32_t *w = pass ? *(uint32_t **)st : (uint32_t *)st;
            for (int i = 0; w && i < 96; i++) {
                const char *c = (const char *)w[i];
                if (w[i] < 0x81000000u || w[i] >= 0x86000000u) // heap de newlib
                    continue;
                int n = 0;
                while (n < 200 && c[n] >= 0x20 && c[n] < 0x7f)
                    n++;
                if (n >= 6 && !c[n])
                    l_error("  %s[%d]: %.200s", pass ? "db" : "vdbe", i, c);
            }
        }
    }
    return r;
}
// diagnóstico #097: "cannot start a transaction within a transaction": SQL que falla al preparar + transacciones
static uintptr_t sq_prep_orig;
static uint32_t p_sqlite3_prepare_v2(uint32_t db, uint32_t sql, uint32_t n, uint32_t pst, uint32_t tail, uint32_t a5) {
    uint32_t r = call_guest6((void *)sq_prep_orig, db, sql, n, pst, tail, a5);
    const char *q = (const char *)sql;
    int tx = q && (!strncasecmp(q, "BEGIN", 5) || !strncasecmp(q, "COMMIT", 6) || !strncasecmp(q, "END", 3) ||
                   !strncasecmp(q, "ROLLBACK", 8));
    if (r || tx) {
        const char *mg = "?";
        int at = -1;
        for (int i = 0; db && i < 64 && at < 0; i++) {
            uint32_t w = ((uint32_t *)db)[i];
            mg = w == 0xa029a697 ? "OPEN" : w == 0xf03b7906 ? "BUSY" : w == 0x9f3c2d33 ? "CLOSED" : w == 0x4b771290 ? "SICK" : NULL;
            if (mg)
                at = i;
        }
        l_error("sqlite3_prepare(db=%08x magic[%d]=%s, %.300s) = %u, stmt %08x", db, at, at < 0 ? "?" : mg,
                q ? q : "(null)", r, pst ? *(uint32_t *)pst : 0);
    }
    if (r && db)
        for (int i = 0; i < 96; i++) {
            uint32_t w = ((uint32_t *)db)[i];
            if (w < 0x81000000u || w >= 0x86000000u)
                continue;
            const char *c = (const char *)w;
            int k = 0;
            while (k < 200 && c[k] >= 0x20 && c[k] < 0x7f)
                k++;
            if (k >= 6 && !c[k])
                l_error("  db[%d]: %.200s", i, c);
        }
    return r;
}

static uintptr_t sq_exec_orig, sq_prepv2_orig;
static uint32_t p_sqlite3_prepare_v2b(uint32_t db, uint32_t sql, uint32_t n, uint32_t pst, uint32_t tail, uint32_t a5) {
    uint32_t r = call_guest6((void *)sq_prepv2_orig, db, sql, n, pst, tail, a5);
    if (r || (pst && !*(uint32_t *)pst))
        l_error("sqlite3_prepare_v2(db=%08x, %.300s) = %u, stmt %08x", db, sql ? (const char *)sql : "(null)", r,
                pst ? *(uint32_t *)pst : 0);
    return r;
}
static uint32_t p_sqlite3_exec(uint32_t db, uint32_t sql, uint32_t cb, uint32_t arg, uint32_t err, uint32_t a5) {
    uint32_t r = call_guest6((void *)sq_exec_orig, db, sql, cb, arg, err, a5);
    if (r || (sql && strncmp((const char *)sql, "CREATE", 6) && strncmp((const char *)sql, "SELECT name, rootpage", 21)))
        l_error("sqlite3_exec(db=%08x, %.300s) = %u", db, sql ? (const char *)sql : "(null)", r);
    return r;
}

static uint32_t p_sqlite3_column_text(uint32_t st, uint32_t col, uint32_t a2, uint32_t a3) {
    uint32_t r = call_guest((void *)sq_text_orig, st, col, a2, a3);
    static int n;
    if (n++ < 30)
        l_info("sqlite3_column_text(%u) = %.60s", col, r ? (const char *)r : "(null)");
    return r;
}

// Igual para Thumb: copia 8 bytes (4 instrucciones de 16 bits independientes de la posición) + ldr.w pc.
static uintptr_t hook_keep_thumb(uint32_t vm, void *dst) {
    uint32_t c[4] = {macho_read_text(vm), macho_read_text(vm + 4), 0xF000F8DF, (vm + 8 + MACHO_SLIDE) | 1};
    uintptr_t orig = macho_code(c, 4);
    macho_hook_addr(vm | 1, dst);
    return orig | 1;
}

// diagnóstico PES 2012: 0x1600b8(this) recibe 0x38; el constructor previo devuelve this según el análisis estático
static uintptr_t f17ef96_orig;
static uint32_t p_17ef96(uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3) {
    l_info("0x17ef96(this=0x%08x, 0x%x, 0x%x, 0x%x)", a0, a1, a2, a3);
    return call_guest((void *)f17ef96_orig, a0, a1, a2, a3);
}
static uintptr_t f1600b8_orig;
static uint32_t p_1600b8(uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3) {
    l_info("0x1600b8(this=0x%08x, 0x%x, 0x%x, 0x%x)", a0, a1, a2, a3);
    return call_guest((void *)f1600b8_orig, a0, a1, a2, a3);
}

// diagnóstico PES 2012: new(0x25b4) (0x8b788 -> s3eMallocBase) termina en this = NULL
static uintptr_t mallocbase_orig;
static void *p_s3eMallocBase(uint32_t n) {
    if (n > 0x4000000) {  // > 64 MB: tamaño basura, ¿de dónde viene?
        uint32_t *sp = (uint32_t *)__builtin_frame_address(0);
        l_error("s3eMallocBase(0x%x): tamaño absurdo", n);
        for (int i = 0, k = 0; i < 1024 && k < 40; i++)
            if (sp[i] >= 0x98001000u && sp[i] < 0x9831e000u)
                l_error("  pila[%d] = 0x%x", i, (unsigned)(sp[i] - MACHO_SLIDE)), k++;
    }
    void *r = (void *)call_guest((void *)mallocbase_orig, n, 0, 0, 0);
    if (!r)
        l_error("s3eMallocBase(0x%x) = NULL", n);
    if (n == 0x30 || n == 0x3c)
        l_info("s3eMallocBase(0x%x) = %p", n, r);
    return r;
}

static void log_ram_drive(const char *where);
// diagnóstico PES 2012: el cargador de imágenes (0xeb114) lee basura sin que haya open(): ¿falla s3eFileOpen?
static uintptr_t fileopen_orig;
static uint32_t p_s3eFileOpen_internal(const char *name, const char *mode, uint32_t a2, uint32_t a3) {
    if (name && !strncmp(name, "ram://", 6)) { // diagnóstico: tabla de drivers de archivos y raíces por unidad
        uint8_t *fs = (uint8_t *)(*(uint32_t *)(0x281b0 + MACHO_SLIDE));
        char **roots = *(char ***)(*(uint32_t *)(0x2a73c + MACHO_SLIDE));
        for (int i = 0; i <= 10; i++) {
            uint8_t *e = fs + i * 0x58;
            l_info("FS[%d] on=%d sw=%d chk=%08x wr=%08x", i, e[0x225], e[0x224], *(uint32_t *)(e + 0x200),
                   *(uint32_t *)(e + 0x1f0));
        }
        for (int i = 0; i < 5; i++)
            l_info("raiz[%d] = %s", i, roots && roots[i] ? roots[i] : "(null)");
    }
    uint32_t r = call_guest((void *)fileopen_orig, (uint32_t)name, (uint32_t)mode, a2, a3);
    l_info("s3eFileOpen(%s, %s) -> 0x%x", name ? name : "(null)", mode ? mode : "(null)", r);
    log_ram_drive("open");
    if (name && (!strchr(name, '.') || !strcmp(name, "ram://database/save.bin"))) { // diagnóstico: quién recopia el save
        uint32_t *sp = (uint32_t *)__builtin_frame_address(0);
        for (int i = 0, k = 0; i < 2048 && k < 40; i++)
            if (sp[i] >= 0x9807df00u && sp[i] < 0x9831e000u) // solo la app (__symbolstub1)
                l_error("  pila[%d] = 0x%x", i, (unsigned)(sp[i] - MACHO_SLIDE)), k++;
    }
    return r;
}

// diagnóstico: todo error interno de Marmalade (dispositivo 1 = FILE) con la pila del juego
static uintptr_t errset_orig;
static uint32_t p_s3eErrorSet_internal(uint32_t dev, uint32_t err, uint32_t level, uint32_t a3) {
    static int n;
    if (dev == 1 && n++ < 40) {
        uint32_t *sp = (uint32_t *)__builtin_frame_address(0);
        l_error("s3eErrorSet(dev=%u, err=%u, nivel=%u)", dev, err, level);
        for (int i = 0, k = 0; i < 256 && k < 6; i++)
            if (sp[i] >= 0x98001000u && sp[i] < 0x9831e000u)
                l_error("  pila[%d] = 0x%x", i, (unsigned)(sp[i] - MACHO_SLIDE)), k++;
    }
    return call_guest((void *)errset_orig, dev, err, level, a3);
}

// diagnóstico #067: s3eFileRead con tamaño 0x9901948c (read() -> EFAULT -> SQLITE_IOERR): ¿quién lo pide?
static uintptr_t fread_orig;
static uint32_t p_s3eFileRead(uint32_t buf, uint32_t sz, uint32_t n, uint32_t f) {
    if ((uint64_t)sz * n > 0x4000000) {
        uint32_t *sp = (uint32_t *)__builtin_frame_address(0);
        l_error("s3eFileRead(%08x, 0x%x, 0x%x, %08x)", buf, sz, n, f);
        for (int i = 0, k = 0; i < 2048 && k < 24; i++)
            if (sp[i] >= 0x98001000u && sp[i] < 0x9831e000u)
                l_error("  pila[%d] = 0x%x", i, (unsigned)(sp[i] - MACHO_SLIDE)), k++;
    }
    return call_guest((void *)fread_orig, buf, sz, n, f);
}

// diagnóstico #068: unixRead (0x289964) de la app recibe amt = 0x9901948c: ¿qué llamador?
static uintptr_t unixread_orig;
static uint32_t p_unixRead(uint32_t f, uint32_t buf, uint32_t amt, uint32_t r3, uint32_t off_lo, uint32_t off_hi) {
    if (amt > 0x4000000) {
        uint32_t *sp = (uint32_t *)__builtin_frame_address(0);
        l_error("unixRead(f=%08x, buf=%08x, amt=0x%x, off=0x%x:%x) ret=%p", f, buf, amt, off_hi, off_lo,
                __builtin_return_address(0));
        for (int i = 0, k = 0; i < 128 && k < 12; i++)
            if (sp[i] >= 0x98001000u && sp[i] < 0x9831e000u)
                l_error("  pila[%d] = 0x%x", i, (unsigned)(sp[i] - MACHO_SLIDE)), k++;
    }
    return call_guest6((void *)unixread_orig, f, buf, amt, r3, off_lo, off_hi);
}

static void log_ram_drive(const char *where) {
    uint8_t *fs = (uint8_t *)(*(uint32_t *)(0x281b0 + MACHO_SLIDE));
    char **roots = *(char ***)(*(uint32_t *)(0x2a73c + MACHO_SLIDE));
    l_info("[%s] ram: raiz=%s on=%d wr=%08x", where, roots && roots[1] ? roots[1] : "(null)", fs[0x58 + 0x225],
           *(uint32_t *)(fs + 0x58 + 0x1f0));
}
static uintptr_t setdrive_orig;
static uint32_t p_s3eFileSetDrivePrefix(const char *prefix, const char *path, uint32_t a2, uint32_t a3) {
    l_info("s3eFileSetDrivePrefix(%s, %s)", prefix ? prefix : "(null)", path ? path : "(null)");
    uint32_t r = call_guest((void *)setdrive_orig, (uint32_t)prefix, (uint32_t)path, a2, a3);
    log_ram_drive("tras SetDrivePrefix");
    return r;
}

// IwGx scratch y skin buffer: el binario original pide 320 KB para scratch y 2048 vértices para skinning.
// Al renderizar varios modelos (jugadores en menús o 22 en partido) y la UI, los buffers se agotaban y
// el asignador de IwGx (0x179634/0x17969a) reescribía vértices activos con ceros y punteros (#118).
// Forzar tamaños generosos (8 MB scratch, 32768 vértices skinning) previene el desborde y la colisión.
static uintptr_t scratch_size_orig;
static uint32_t p_IwGxSetScratchBufferSize(uint32_t size, uint32_t a1, uint32_t a2, uint32_t a3) {
    uint32_t new_size = size;
    if (new_size < 8 * 1024 * 1024)
        new_size = 8 * 1024 * 1024;
    l_info("IwGxSetScratchBufferSize(%u) -> %u", (unsigned)size, (unsigned)new_size);
    return call_guest((void *)scratch_size_orig, new_size, a1, a2, a3);
}

static uintptr_t skin_size_orig;
static uint32_t p_IwGxSetSkinBufferSize(uint32_t count, uint32_t a1, uint32_t a2, uint32_t a3) {
    uint32_t new_count = count;
    if (new_count < 32768)
        new_count = 32768;
    l_info("IwGxSetSkinBufferSize(%u) -> %u", (unsigned)count, (unsigned)new_count);
    return call_guest((void *)skin_size_orig, new_count, a1, a2, a3);
}

void patch_init(void) {
    scratch_size_orig = hook_keep_arm(0xf7314, (void *)p_IwGxSetScratchBufferSize);
    skin_size_orig = hook_keep_arm(0xfb128, (void *)p_IwGxSetSkinBufferSize);
    setdrive_orig = hook_keep("__Z21s3eFileSetDrivePrefixPKcS0_", (void *)p_s3eFileSetDrivePrefix);
    unixread_orig = hook_keep_arm(0x289964, (void *)p_unixRead);
    fread_orig = hook_keep("_s3eFileRead", (void *)p_s3eFileRead);
    errset_orig = hook_keep("_s3eErrorSet_internal", (void *)p_s3eErrorSet_internal);
    fileopen_orig = hook_keep("_s3eFileOpen_internal", (void *)p_s3eFileOpen_internal);
    mallocbase_orig = hook_keep("_s3eMallocBase", (void *)p_s3eMallocBase);
    f1600b8_orig = hook_keep_thumb(0x1600b8, (void *)p_1600b8);
    sq_step_orig = hook_keep_arm(0x2871cc, (void *)p_sqlite3_step);
    sq_prepv2_orig = hook_keep_arm(0x286fc0, (void *)p_sqlite3_prepare_v2b);
    sq_exec_orig = hook_keep_arm(0x286a60, (void *)p_sqlite3_exec);
    sq_prep_orig = hook_keep_arm(0x286fa0, (void *)p_sqlite3_prepare_v2);
    sq_text_orig = hook_keep_arm(0x28679c, (void *)p_sqlite3_column_text);
    if (macho_hook("__Z13s3eDecodeDataj", (void *)p_s3eDecodeData) == 0)
        l_info("s3eDecodeData anulado (datos s3e descifrados en staging)");
    fix_swp();
    disable_sys_stack_switch();
    force_gles1();
}
