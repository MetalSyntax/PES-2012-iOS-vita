// Parches de PES 2012 (iOS, Marmalade enlazado en el Mach-O). Solo lo que el análisis estático
// justifica; el resto de imports sin implementar sale en el log como "unimpl".
#include <stdint.h>
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

const import_entry patch_imports[] = {
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

void patch_init(void) {
    disable_sys_stack_switch();
    force_gles1();
}
