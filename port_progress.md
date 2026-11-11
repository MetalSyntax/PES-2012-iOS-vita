# Registro de Progreso — PES 2012 iOS (PS Vita)

## Fase 1: Scaffold iOS (2026-11-11)

El port Android sigue en `../Incompletes/PES-2012-vita` (Marmalade `.s3e`, Bug #18 + marmalade-vita-core, a probar).
Se arranca desde `PES2012_v1.0.2_os30.ipa`: Mach-O armv6 desencriptado (`cryptid 0`), Marmalade enlazado estático, assets sueltos.

- [x] Se reemplazó la plantilla de `psvita-toolkit init --ipa` (no compilaba, sin ObjC) por el runtime iOS de
  **HAWX-vita** con su `tools/sync_runtime.sh`. Se borraron `coreaudio.c`/`darwin.h` de la plantilla. `vendor/vitaGL` (SOFTFP_ABI=1).
- [x] `source/port_config.h`: slug `pes2012ios`, exec `PES2012`, rotación 1, iOS 3.1.3 / iPhone2,1 (como PES 2011 iOS).
- [x] `tools/stage_data.sh`: `ux0_data/pes2012ios/` con `game_app/`, `rebase.bin` (55757 palabras), `binds.bin`
  (680 símbolos), `nib.txt` (`MainWindow.nib`). Música en `.group.bin` (nada que convertir).
- [x] `docs/macho_report.txt` regenerado con rutas propias (el anterior apuntaba a `../Incompletes/PES-2012-iOS-vita`).
- [x] `tools/gen_cxx.py`: 4 imports C++, todos resueltos.
- [x] Análisis Marmalade (ver PORTING_PLAN §2): `_s3eStackSwitchReadConfig` fuerza `SysStackSwitch=1` →
  `patch.c` lo apaga (0x4116c `mov r3,#1` → `#0`, verificado); `_s3eThreadCreate_platform` usa `pthread_attr_setstack`
  con stack del heap → se ignora la dirección. Heap RWX / SoundPlayer de `marmalade-vita-core` no aplican al Mach-O.
- [x] `app.icf` embebido pide `SysGlesVersion=2`; `_s3eGLInit_platform` cae a ES1 si `initWithAPI:2` da nil →
  `patch.c` lo fuerza (el runtime no exporta GLES2).
- [x] `patch.c`: además zlib (`inflate*`), `tzset`, `statvfs`.
- [x] Build Debug OK: `build/eboot.bin` (1.4 MB), `build/pes2012ios.vpk`.

## PENDIENTE EN CONSOLA (corrida iOS #1)

1. Subir los datos: `python3 -I tools/deploy_data.py` (sube `ux0_data/pes2012ios/` → `ux0:data/pes2012ios/`, ~640 MB).
2. Instalar `build/pes2012ios.vpk` (TITLEID `PES201201`, convive con el Android `PES201200`).
3. Abrir el juego, traer logs y dump con `python3 -I tools/deploy_data.py --logs`. Decir si crasheó o quedó congelado.
4. Qué mirar en el log: `SysStackSwitch forced off (0x4116c)`; `EAGLContext initWithAPI:2 -> nil` seguido de
   `initWithAPI:1`; `pthread_create(...)` de los hilos s3e; `unimpl: _AudioFileOpenWithCallbacks` (música).
5. En un crash: PC − 0x98000000 = dirección en el Mach-O (`otool -tV`, tiene símbolos `_s3e*`); dirección de datos
   < 0x02000000 = rebase faltante (buscar en `ux0_data/pes2012ios/report.txt`). Stop reason `0x10006` = SP fuera del
   stack del hilo (fibras s3e, ver PORTING_PLAN §2).
