# Plan de Port — PES 2012 (iOS → PS Vita)

## 0. Contexto

- **Origen:** `PES2012_v1.0.2_os30.ipa` (`com.konami-europe.pes2012`, v1.0.2, MinimumOS 3.0, SDK 3.2, universal iPhone/iPad, `MainWindow.nib`).
- **TITLEID:** `PES201201`, slug `pes2012ios` (convive con el port Android `PES201200` / `pes2012`).
- **Por qué iOS y no Android:** el APK es Marmalade: el juego real es un `.s3e` que se carga y reloca en runtime
  (memoria RWX, `SysStackSwitch`, ver `../Incompletes/PES-2012-vita/port_progress.md`, Bug #18 y fase marmalade-vita-core).
  En el IPA, Marmalade está **enlazado estáticamente** en el Mach-O: no hay `.s3e` que cargar.

## 1. Binario (`game_app/PES2012`)

- Mach-O `MH_EXECUTE` armv6, delgado, `cryptid 0`, no PIE, **con símbolos** del loader s3e. Entry `0x42b8`,
  imagen `[0x1000, 0x102c000)`.
- Layout Marmalade iOS: loader s3e en `__TEXT,__text` (450 KB, símbolos `_s3e*`), código de la app en
  `__TEXT,__symbolstub1` (2.7 MB, mismo papel que `s3e_code` en PES 2011 iOS) y datos en `__S3E_DATA,s3e_data` (0x1000000).
  El `app.icf` va embebido en el binario.
- `ios_prep.py`: 55757 palabras de rebase (modo clásico heurístico), 680 símbolos en 1076 slots, cobertura de
  `__text` 92.3 %, 12877 dudosas. **El código de `__symbolstub1` se rebasea con la heurística de datos (no se desensambla)**:
  si un crash cae en esa zona con un puntero < 0x02000000, buscarlo en `report.txt`.
- C++: 4 imports de libstdc++, todos resueltos (`source/cxx_imports.c`).
- Frameworks: Foundation/CoreFoundation, UIKit, OpenGLES, QuartzCore, CoreGraphics, AudioToolbox/CoreAudio, AVFoundation
  (captura), CoreMedia/CoreVideo, MediaPlayer, GameKit, StoreKit, MessageUI, CoreLocation, AddressBook,
  MobileCoreServices, SystemConfiguration, CFNetwork, libz.
- `docs/macho_report.txt`: inventario de `tools/macho_prep.py` regenerado con rutas propias (solo referencia).

## 2. Marmalade y `marmalade-vita-core`

El núcleo común resuelve tres cosas del `.s3e` en Android. En el Mach-O iOS:

| Problema Android | ¿Aplica al IPA? | Qué se hizo |
|---|---|---|
| Heap RWX para el `.s3e` relocado (`mcore_heap_make_rwx`) | **No**: el código ya está en `__TEXT`, lo mapea el loader Mach-O | nada |
| `SysStackSwitch`: llamadas s3e con SP en un stack del heap (stop `0x10006`) | **Sí**: `_s3eStackSwitchReadConfig` (0x4113c) lo fuerza a 1 sin leer el icf | `patch.c` cambia `mov r3,#1` (0x4116c) por `mov r3,#0`, verificando la instrucción |
| Hilos con stack propio | **Sí**: `_s3eThreadCreate_platform` usa `pthread_attr_setstack` con memoria del heap | `patch.c`: se ignora la dirección, solo se respeta el tamaño (stack real de la Vita) |
| SoundPlayer / `audioPlay` por JNI | **No**: en iOS s3eSound/s3eAudio van por AudioQueue/AudioFile del runtime | nada |

Por eso el núcleo no se integra (no hay `.s3e`, ni JNI, ni `valloc` del motor que mover a la arena).
Queda abierto: las fibras de s3e (`_s3eFibreCreate`) también corren en stacks del heap; si un hilo de fibra hace
una syscall bloqueante, el stop `0x10006` reaparece (ahí sí la arena de `mcore_run_game_thread` sería la salida).

## 3. GLES

`app.icf` embebido: `SysGlesVersion=2` en iPhone, `=1` en iPad2/Android. `_s3eGLInit_platform` (0x4b57c) prueba
`initWithAPI:2` y si da nil usa `initWithAPI:1`. El runtime no exporta GLES2, así que `patch.c` devuelve nil para ES2
(IwGx queda en el camino ES1, el mismo que usa el port Android). `DispFixRot=2`, ventana 480x320.

## 4. Assets (sueltos en el bundle)

- 790 `*.group.bin` (formato Konami, igual que PES 2011): `menu/`, `hud/`, `body/`, `stadium/`, `ball/`, `sound/`, `music/`.
- `database/`, `string/`, `scoreloop/` (SDK online: no disponible), `MainWindow.nib` → `nib.txt`.
- La música está en `music/*.group.bin`, no en `.m4a`: `stage_data.sh` no convierte nada. El juego importa
  `AudioFileOpenWithCallbacks` (AAC en memoria), que el runtime no tiene: probablemente sin música en la corrida #1.

## 5. Imports

- `patch.c`: zlib (`inflate*`), `tzset`, `pthread_attr_setstack`, `statvfs` (1 GB libre).
- AddressBook, AVCapture, GameKit (`GKSession`), StoreKit, CoreLocation, MessageUI: clases vacías / funciones `unimpl`
  que devuelven 0/nil = "no disponible".
- Sin implementar (log `unimpl`): GLES2 completo (no se usa con ES1 forzado), `CFNetService*` (Bonjour), `AudioFileOpenWithCallbacks`,
  `AudioQueueNewInput`, `dispatch_set_context`/`finalizer_f`.

## 6. Checklist

- [x] Scaffold iOS (runtime de HAWX-vita), `port_config.h`, `patch.c`, staging y build OK.
- [ ] Corrida #1 en consola: `SysStackSwitch forced off` en el log y llegar a `applicationDidFinishLaunching` sin crash.
- [ ] Contexto GL ES1 + primer frame (confirmar `PORT_ROTATION` con `DispFixRot=2`).
- [ ] Carga de `database/*.bin`, `*.group.bin` y `Documents/` (partidas).
- [ ] Audio (efectos por AudioQueue; música por `AudioFileOpenWithCallbacks`).
- [ ] Controles (`controls.txt`, toques ↔ botones).
