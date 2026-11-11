// Generado por tools/gen_cxx.py: imports C++ del juego -> libstdc++ de VitaSDK.
// No editar a mano (volver a correr gen_cxx.py con el report.txt de ios_prep.py).
#include <string.h>

#include "imports.h"

extern char cxx_0 __asm__("_ZdaPv"); // operator delete[](void*)
extern char cxx_1 __asm__("_ZdlPv"); // operator delete(void*)
extern char cxx_2 __asm__("_Znaj"); // operator new[](unsigned long)
extern char cxx_3 __asm__("_Znwj"); // operator new(unsigned long)

const import_entry cxx_imports[] = {
    {"__ZdaPv", (uintptr_t)&cxx_0, 0},
    {"__ZdlPv", (uintptr_t)&cxx_1, 0},
    {"__Znam", (uintptr_t)&cxx_2, 0},
    {"__Znwm", (uintptr_t)&cxx_3, 0},
};
TABLE_END(cxx)

// Lo que no tiene equivalente queda en el log como "unimpl".
const import_entry *cxx_import(const char *name) { (void)name; return NULL; }
