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

## Corridas en consola

- **#001**: FATAL `rebase.bin` no encontrado → la subida de datos había quedado a medias; resubido con `deploy_data.py`.
- **#002**: llega a `run loop principal`; crash en el hilo s3e (`pthread`), Undefined instruction en PC 0x9802bcdc =
  `swp r2,r1,[r0]` de `_s3eGlobalLock_asm_arm`. SWP (ARMv6) no existe en la Vita. 16 sitios en `__text` (4 en
  `_s3eGlobalLock*`, 12 en los trampolines de stack switch 0x3ab88–0x3ae54; el escaneo crudo coincide con otool, sin
  falsos positivos) → `patch.c fix_swp()`: cada SWP → `B` a un stub LDREX/STREX en el arena de thunks
  (`macho_code()` nuevo en `macho.c`, pendiente de llevar a HAWX-vita/sync_runtime). Log esperado: `SWP -> LDREX/STREX: 16 sitios`.
- **#003**: `fix_swp` solo parcheó 4 de 16 (rechazaba base `lr`; ahora solo excluye sp/pc). Crash Data abort en
  `_s3eConfigReadFile` 0x1c0fc: `ldrb [lr,#-1]` con lr = 0x78742 sin slide. Son 5 literales que apuntan al
  *interior* de `__cstring` (hash inlineado desde "S3E"+2, "SCREENSIZE"...), que `ios_prep.py` solo aceptaba en
  inicios de cadena → regla nueva: literal dentro de `__cstring` con uso no entero = puntero (+5 rebases, exactamente esos).
- **#004**: SWP 16/16 OK; pasa `_s3eConfigReadFile`. Data abort en `_s3eExtReadConfig` 0x23448 `ldr r3,[r4,r5]` con
  r5 = 0x00a06c88 (`__data`) sin slide. `_classify_use` solo miraba `mem.base`: con r5 como *índice* seguía hasta
  `popeq {r4,r5,r7,pc}` y lo tomaba por PIC ('pcrel'). Ahora base o índice = 'ptr' (+1 rebase, exactamente ese).
- **#005**: sin crash. Tras `setStatusBarOrientation:3` el juego muestra un UIAlertView vacío y llama `exit(0)`
  (probable `s3eDisplayError_real`). Lo crea con `initWithFrame:` + `setTitle:`/`setMessage:`, que eran no-op en
  `uikit.c` → implementados para que el log muestre el error.
- **#006**: el alert dice "Insufficient memory to start application. Not enough memory to begin loading s3e file."
  (`_s3eExecInsufficientMemory`, flag `[g+0xb8]` que ponen `s3eMallocOS`/`s3eMallocBase` cuando
  `_s3eMallocOS_platform` = `malloc` devuelve NULL). icf: `MemSize=35000000`; newlib heap = 300 MB, así que no
  cuadra → `patch.c` envuelve malloc/calloc/realloc y loguea tamaño + `mallinfo` cuando fallan.
- **#007**: `malloc(2550206832)` = `malloc(0x98014470)`: un tamaño con slide. `ios_prep.py` rebaseaba 27 palabras de
  `__S3E_DATA` (5 campos de la cabecera XE3U + 22 bytes comprimidos que caían en el rango de `__TEXT`). Ese segmento
  es el .s3e embebido, sin punteros Mach-O → excluido de la heurística de datos (−27 rebases, solo esos).
  Los wrappers OOM de malloc en `patch.c` quedan (solo loguean si falla).
- **#008**: mismo `malloc(0x98014470)` con el rebase nuevo (55736). Otro tamaño rebaseado: `s3eMemoryInit` 0x30c38
  `ldr r0,=0x11170; bl _s3eMallocOS_platform` (0x11170 coincide con un inicio de función). `ios_prep.py`: los
  `_s3eMalloc*/_s3eRealloc*/_s3eCalloc*/_s3eEdk*` cuentan como asignadores (args = 'int') → −1 rebase (0x30d04).
  El +0x3300 hasta 0x14470 no está explicado: `log_oom` ahora vuelca direcciones de `__text` de la pila.
- **#009**: sin OOM (rebase 55735). Data abort en Thumb 0x25ede8 (`__symbolstub1`, que es código Thumb del juego
  con flags 0 → `ios_prep.py` lo trata como datos): `ldr r0,=0x0102ac88` sin slide. Apunta a la cola de `__S3E_DATA`
  (sección hasta 0x10111d0, segmento hasta 0x102c000 = bss del .s3e). `dhi` se calculaba por secciones → ahora
  hasta el final del segmento: +1203 rebases, todos stub1 → cola S3E, ninguno quitado.
- **#010**: PC=0 desde Thumb 0x25edea: tabla de la extensión s3eThread (`[0x0102ac88+0x24]`) en 0. El check de
  disponibilidad (0x111d48) leía flags basura en 0x0100fa14: los datos de la app en `__S3E_DATA`
  (0x1005c1c, 0xb5b4 bytes) van **cifrados**; `s3eExecExecute` → `s3eDecodeData(key)` con key = `s3eGetCodeHash()` =
  `dladdr` + hash `h*31+b` (desde 1234) sobre [__text, fin de __symbolstub1) en direcciones absolutas. Nuestro
  `dladdr` = 0 → clave 0 → basura. Fix: `ios_prep.py s3e_decrypt()` descifra el binario de staging (clave
  0x8a948b1e, validada: flags 0, strings `com.konami.pes2012.award.*`) y analiza sus punteros (+1533 rebases);
  `patch.c` anula `s3eDecodeData`. Además el rango de datos exige un segmento real (−517 rebases falsos dentro del
  código Thumb de stub1 que caían en el hueco 0xa10000–0x1000000). PES2012 de staging subido a mano (mismo tamaño).
- **#011**: datos s3e OK (pasa el check de s3eThread). Data abort en memset(0xdeadbeef) desde Thumb 0xbc184: el
  rebase falso 0xbc1a0 = `bne` + `movs r1,r5` (0x0029d10b) convertía `movs r1,r5` en `str r0,[sp,#164]`.
  **`__symbolstub1` es la app (ARM ~60 % + Thumb) + rodata (RTTI, vtables, ~0x2b8000–0x2f0000) + thunks ARM.**
  `ios_prep.py`: CodeScan recorre también stub1 (raíces: punteros Thumb de __DATA/S3E, tablas, prólogos ARM/Thumb en
  huecos; cobertura 71.6 %); las palabras sobre código no pasan por la heurística de datos salvo destino fuerte (datos,
  cadenas, función conocida, tabla alineada o inicio de cadena RTTI) o corrida de punteros Thumb (vtable); tablas de
  salto solo en __text; literales de stub1 → __text exigen inicio de función real (86400 y 77608 eran rebaseados).
  −4170 rebases falsos (todos en stub1), 0 nuevos; verificados: tablas RTTI/vtables, thunks, tabla s3eThread.
  Quedan ~2600 no-literales en la zona de código de stub1, en parte legítimos (tablas de switch). Guía: WaW Zombies
  iOS (`Zips/waw zombies assets by but.vpk`, `excludes_thumb_instruction_pairs`).
- **#012**: crash idéntico a #011 → el diagnóstico de #011 era incorrecto (0xbc1a0 está en la rama de error). Causa
  real en la misma función: 0xbc1c0 = segunda mitad de `blx` + `movs r0,r4` (0x0020e8ac) seguía rebaseado → la función
  no devolvía el puntero (r0 basura = 0xdeadbeef); también 0xbc1b8 (`str` + `movs r1,r6`). Los salvaba la excepción
  "destino alineado no-código" (1 de cada 4 pares Thumb la cumple), que existía porque el desensamblado invadía tablas
  RTTI (cadena de basura desde 0x2d6ee2). Fix en CodeScan: no decodificar palabras en corridas de ≥3 valores-imagen
  fuera de __text; raíces Thumb heurísticas exigen `push`; fuera la excepción "alineado". Total vs #011: −3314, 0 nuevos;
  tablas RTTI/vtables/thunks conservadas.
- **#013**: avanza (pasa la reserva de 0x25b640). Mismo patrón en 0x25b710: 0x25b740 = `bx r3` + `movs r0,r4`
  (0x00204718) y 0x25b738 rebaseados → r4 = 0xdeadbeef → ldr en 0xd8864. La función no se descubría: su prólogo
  `push {r4-r6,lr}` + `movs r4,r0` = 0x0004b570 tras el pool de literales anterior parecía una tabla de 3 punteros.
  `is_table` exime un `push` Thumb seguido de código (no de otro puntero); el barrido de prólogos usa `is_table` en vez
  de "parece puntero"; destino exacto a prólogo ARM = fuerte (vtables ARM). Total vs #011: −4041, 0 nuevos.
- **#014**: avanza (pasa 0x25b710). Data abort en el bucle de constructores estáticos de la app (Thumb 0x7f8b4):
  `ldr r4,=0x281014; add r4, pc` → literal PIC rebaseado. `_classify_use` no veía el `add rX, pc` Thumb de 2 operandos,
  y en stub1 la heurística de datos rebaseaba igual los literales (ahora un literal conocido lo decide la ruta de
  literales). `_classify_use` se detiene en saltos incondicionales (thunk `ldr ip; ldr pc`: ip = argumento; antes salía
  'int' al "decodificar" los literales siguientes); literal 'int' que apunta a un inicio de función = puntero
  (cmp contra callback). vs #014: −36 (PIC), +1 en __text (0x4cf98 → __DATA). vs #011: −4077, +1.
- **#015–#032 (ciclo automático `tools/hottest.py` + vitacompanion)**:
  - `ios_prep.py`: PIC Thumb `add rX, pc`; literales de stub1 los decide la ruta de literales; `_classify_use` se
    detiene en saltos incondicionales (ip de thunks); literal 'int' a inicio de función = puntero (cmp contra callback);
    `is_table` exige un puntero plausible o firma de switch (≥3 entradas cercanas alineadas), solo frena al empezar
    desde una raíz (no en flujo secuencial); vtables de funciones ARM hoja; punteros a tablas de punteros (RTTI);
    tablas `ldrcc/ldrls pc,[pc,rX,lsl#2]` también en stub1 (con todas las entradas cercanas; ldrcc = n entradas);
    `___switch8` de Apple (0x204dc8, 0x28e080): tabla en línea tras el bl, case = lr + 2*byte;
    recursos incrustados en datos s3e (BMP/PNG) no se rebasean (BMP 32x32 en 0x100e058 → malloc de 758 MB).
    vs #011: −4699 rebases falsos, +1.
  - Runtime: `glGetIntegerv` con puntero inválido se registra y no escribe (diagnóstico); `tools/deploy_data.py --logs`
    baja solo el dump más reciente; `fopen`/`open`/`stat` se registran.
  - **rom://**: Marmalade (s3eExecSetupPaths) usa la 1ª entrada de HOME que termine en ".app"; `darwin.c` muestra
    `game_app` como `game_app.app` en readdir de DATA_PATH y lo deshace en `darwin_path`.
  - **Inodo de 64 bits**: este binario usa `struct dirent` (d_name en +21) y `struct stat` de 108 bytes (st_mode en +4):
    `port_config.h DARWIN_INODE64 1`. Con eso abre `string/sp.bin` y `string/common.bin`.
  - #033: nombre truncado "soun": el strcpy palabra a palabra de la app (0x7ec2c) usa la constante 0x01010101, que cae
    en __S3E_DATA y se rebaseaba → nunca se rebasea (−22, todas constantes). Abre `sound/menu/soundMenu.group.bin`.
  - **Pendiente (#034)**: al deserializar ese .group.bin salen tamaños basura (s3eMallocBase(-4), (0xfffb0006)).
    Siguiente paso: hook de s3eFileRead para ver qué bytes llegan y comparar con el archivo; sospechosos: otro rebase en
    código/constantes del lector (IwSerialise) o un import de E/S (read/lseek) con semántica distinta.
  - Diagnósticos activos en patch.c (quitar al cerrar): hooks de s3eMallocBase, s3eFileOpen_internal y 0x1600b8;
    log de malloc(0x25b4); glGetIntegerv con puntero inválido. darwin.c registra fopen/open/stat.
- **#035–#065** (ciclo automático; ver `tools/hottest.py`, `tools/dump_threads.py`):
  - **lseek/truncate de Darwin**: off_t de 64 bits sin alinear (r1:r2, whence en r3, retorno r0:r1) → `darwin.c d_lseek`,
    `d_truncate`. Con eso el .group.bin de sonido deja de dar tamaños basura.
  - **ram://** no se registraba: "ram://" en __TEXT,__const tenía "://\0" (0x002f2f3a) rebaseado → palabras de texto en
    __TEXT no se rebasean si la palabra anterior también es texto. (Se recuperaron ~120 fragmentos de cadenas.)
  - Tablas de enteros: `u16_table` (vecindad de pares chicos, sin contar valores dentro de datos) y constantes de SQLite
    (`sqlite_const`: zona sqlite3UpperToLower..keywords, solo palabras con todos los bytes < 0x80).
  - `plausible()`: cadenas también en dirección impar, dentro de cadenas (sufijos fusionados, UTF-8), funciones Thumb
    hoja (tras bx/pop pc) y ARM hoja (tras bx rX / pop pc). Bisección en consola (`bisect_rebase.py` en scratchpad)
    encontró 0xd965c (par Thumb reintroducido) → la regla de código no usa la excepción "hoja".
  - Watchdog (`PORT_WATCHDOG 20` en port_config, `uikit.c`): si el run loop no gira, crash sin log → dump con todos los
    hilos. Ojo: un crash de cualquier hilo detiene el proceso (el log "se corta" sin que sea cuelgue).
  - **Estado (#065)**: el juego llega a la base de datos. `save.bin` (SQLite) se copia rom→ram (Documents/database) y el
    juego valida con `PRAGMA integrity_check` (FUN_0015e98c) → falla → borra y recopia **encima del archivo abierto** →
    queries fallan → crash en 0x2450b0 (stmt NULL). Diagnóstico actual: `sqlite3_step(NULL)=21` y una lectura con error
    en `s3eFileRead_platform` (read() < 0, err=9) → `SQLITE_IOERR` (10). Siguiente: ver errno/fd de ese read()
    (¿fd cerrado por la recopia? ¿O_RDWR?), y comprobar si integrity_check falla por I/O o por el motor.
  - Diagnósticos activos (quitar al cerrar): hooks en patch.c (s3eMallocBase, s3eFileOpen_internal, s3eErrorSet,
    s3eFileSetDrivePrefix, 0x1600b8, sqlite3_step/column_text), logs de open/stat/fopen en darwin.c.
- **#066**: `logs/` reorganizado: históricos en `logs/log/` (*.log) y `logs/dmp/` (dumps + análisis); `hottest.py`
  archiva al inicio de cada ciclo (`deploy_data.py --archive`) y `--logs` no rebaja lo archivado. Diagnóstico:
  `darwin.c d_read` loguea read() fallido → `read(fd=3, buf, 2567017612) = -1 errno=14` (EFAULT): tamaño 0x9901948c
  (valor con slide), no un problema de fd ni de la recopia del save.
- **#067**: hook de `s3eFileRead` (tamaño > 64 MB): `s3eFileRead(buf, 1, 0x9901948c, f)` desde la libc de la app:
  `unixRead` (0x289964) → `seekAndRead` (0x25f9d8) → `read` glue Thumb 0x25c1fc → 0xbc8b4 → s3eFileRead. Las
  direcciones de la pila eran viejas (0x258514 pasa 16 bytes, correcto).
- **#068**: hook en `unixRead`: amt llega bien (100); ahora `read(fd, buf=0x64, n=basura)`. La corrupción está en
  0xbc8b4 (read de la app): rebases falsos en 0xbc8e8 (`movs r3,r0; movs r2,r4` = 0x00220003 → 2º halfword pasa a
  `str r0,[sp,#136]`, r2 = basura) y 0xbc8ec (`movs r1,#1; movs r0,r5` → `ldr r0,[sp,#160]`). CodeScan sí los cubre;
  los rebaseaba `s1_vtable()`: dos pares Thumb seguidos parecen punteros Thumb impares a stub1. Fix: el destino
  tiene que ser función conocida o `push {..,lr}` (0xB5). −73 rebases, todos instrucciones decodificadas, ninguno
  en la zona RTTI/vtables (0x2b8000–0x2f0000). rebase 52902.
- **#069**: **la base de datos pasa** (integrity_check / queries OK) y el juego abre `menu/sd/global.group.bin`.
  Queda un `sqlite3_step(NULL)=21` aislado. Crash nuevo: Data abort en Thumb 0xf6614 (lr 0xf6613), r7 = 0x990136a4.
- **#070**: crash de #069 en 0xf6614: deserializador Iw lee 2 u32 de `global.group.bin` y llama a la fábrica de
  clases 0xfda8c (tabla {hash, crear}); devolvió NULL. Diagnóstico con la fábrica reimplementada en C: el hook miraba
  0x101d62c (literal mal leído; es 0x0100d62c) → sin datos útiles. Corrida cortada a los 14 s sin dump (timing de la
  recopia del save).
- **#071**: misma dirección errónea del hook → mismo crash 0xf6614 (sin log de la fábrica).
- **#072**: hook con 0x100d62c: ningún hash faltante, el crash pasa a 0x1f9e6c. Causa del NULL de #069/#071:
  0xfda8c rebaseada (`push {r4-r7}; movs r7,r1` = 0x000fb4f0 → `str r0,[sp,#60]`, r7 basura = argumento del
  creador). La raíz 0xfda8c (destino de `bl` desde 0xf660e) la frenaba `is_table()`: pool de literales previo +
  la 1ª palabra = corrida de 3 valores-imagen; la excepción de prólogo solo aceptaba `push {..,lr}` (0xB5). Fix:
  también 0xB4 (push sin lr, hoja). −2 rebases (0xfda8c, 0x1282fc), 0 nuevos. rebase 52900. Hook de la fábrica quitado.
- **#073**: fábrica original OK (camino nuevo: objetos de UI creados a los 5.9 s, antes de la base de datos). Crash
  en 0x1f9e6c (búsqueda por la cadena de padres, objeto con vtable 0). Llamador 0x2b59f4 (propiedad
  "eventHandlers"): 0x2b59fc = `bpl` + `movs r7,r0` (0x0007d539) rebaseado → `ldr r0,[sp,#28]`, r7 basura.
  `s1_strong()` aceptaba cualquier destino en `__TEXT,__const` (0x7d539 = ceros, dirección impar). Fix: en
  __const solo destinos alineados a 4. −2 rebases (0x157bd8, 0x2b59fc, ambos pares Thumb), 0 nuevos.
- **#074**: pasa la UI (carga `menu/sd/global.group.bin` entero) y abre `globalPES.group.bin`. Crash en 0x18bcd0:
  0x18bcc8 = `ldr r1,[r5,#8]; lsls r0,r4,#2` (0x00a068a9 → __DATA) rebaseado → `ldr r0,[sp,#640]`. `s1_strong()`
  aceptaba cualquier destino en datos sobre código decodificado: 95 palabras así, 69 en corridas (tablas reales de
  S3E, p. ej. 0x2173ac/0x218624/0x2227b0) y 26 aisladas (23 pares Thumb evidentes). Fix `s1_code_ptr()`: destino en
  datos solo en corrida, o alineado con un vecino de dato (chico, valor-imagen, texto). −23 rebases (exactamente
  esas), 0 nuevos; quedan 0x1a3a68, 0x2173a0, 0x222714 (vecinos 0 / float+0x7df00 / texto).
- **#075**: rebase 52875. Sin crash en 50 s: carga `global`, `globalPES` y abre `menu/sd/menuLogo.group.bin`;
  el log se corta a los 14.7 s (no hay "vivo" a los 20 s) y no hay dump (ni en la Vita).
- **#076** (75 s, sin redeploy): ahora sí dump. Crash en el hash djb2 de Iw (0xd97b6, nombre NULL) desde `setName`
  0x11a65e ← 0x133536 (setName(this) y setName(this+0xa0)). 0x133540 = `movs r0,r4; movs r1,r5` = 0x00290020
  rebaseado → `ldr r0,[sp,#164]`. Lo aceptaba `s1_strong()` por "apunta justo a un prólogo ARM" (0x290020 =
  `push {r4,lr}`); 0x290020 aparece 9 veces sobre código. Fix en `s1_code_ptr()`: si el único motivo es el prólogo
  ARM, el vecino tiene que ser dato (chico, texto, __DATA, prólogo, función). −13 rebases (todos pares Thumb),
  0 nuevos; se conservan 0x1a3a7c/0x1a3a8c/0x1d30cc/0x2173a8 (vtables/tablas con vecinos de dato).
- **#077**: rebase 52862. Pasa `setName`; crash en 0x1f893c (`ldmia r5!` con r1 = 0x6e074269, argumento basura;
  función de vtable usada por ~20 clases). Llamador directo 0x203994 ya arreglado en #076; la función vecina
  0x2039a0 tenía 0x2039a4 (`movs r0,r1; movs r1,r2` → 0x110008) y 0x2039a8 (`beq; movs r0,r3` → 0x18d001)
  rebaseados porque apuntan justo a funciones conocidas. Fix: en `s1_code_ptr()` todo destino sobre código
  decodificado (función, prólogo, cadena, __const) exige un vecino de dato; un valor par hacia una función Thumb
  nunca es puntero. −46 rebases (0 nuevos): la mayoría prólogos Thumb (`push {r2-r6,lr}; movs r4,r0` = 0x0004b57c,
  tras `pop {r3}; bx r3`) cuyo 2º halfword quedaba roto.
- **#078**: rebase 52816. Sin crash: el log se corta a los 14.7 s (etapa de la base de datos) sin dump.
- **#079** (100 s, sin redeploy): igual, corte a los 14.1 s tras abrir `menuLogo.group.bin`, sin dump.
- **#080**: watchdog loguea la edad del tick cada 2 s: a los 13.9 s el run loop está sano (13 ms) y después ningún
  hilo escribe más.
- **#081/#082**: prueba manual: con el juego "corriendo", el FTP lee el log sin problema (la E/S de ux0 no está
  colgada) y el log no crece; corte otra vez a ~14.7 s.
- **#083**: `main.c` loguea el espacio libre de ux0 (`sceIoDevctl 0x3001`): 891 MB libres; el juego solo escribe
  save.bin (172 KB) antes del corte → no es disco lleno.
- **#084**: heartbeat del watchdog a `logs/wd.txt` con `sceIoWrite` directo (sin el mutex del log): también se
  detiene a los ~14 s → se congela el proceso entero.
- **#085**: `wd.txt` con mallinfo + `sceKernelGetFreeMemorySize`: heap 32 MB estable, 18 MB libres → no es memoria.
  En el dump de #076 hay solo 6 hilos (principal, CommonDialog, GC de vitaGL, GxmDisplayQueue, carga, watchdog).
  El usuario confirma en pantalla: negra y congelada, sin error ni vuelta al LiveArea.
- **#086**: manejador de excepciones de kubridge (`kuKernelRegisterExceptionHandler`, contexto a `logs/exc.txt`).
  Esta corrida no se congeló: run loop vivo hasta 38 s, sin excepción; el hilo de carga mudo tras `menuLogo`.
- **#087/#088**: `PORT_DUMP_AT 30` (dump forzado): sin dump. El manejador retornaba y kubridge reintenta la
  instrucción → bucle infinito de excepciones del watchdog (exc.txt lleno). Fix: el manejador se libera
  (`kuKernelReleaseExceptionHandler`) antes de retornar → el reintento va al coredump normal.
- **#089**: dump forzado OK. El hilo de carga está en un bucle infinito en 0x2039ac (`parent` de 0x2039a0, el de
  #077): 0x2039a4/0x2039a8 seguían rebaseados. Dos causas en `ios_prep.py`: (1) la regla "valor par → función
  Thumb" de #077 nunca actuaba (`v & 1 == 0` es `v & (1 == 0)` en Python; ahora `not v & 1`); (2) la función no se
  decodificaba: hoja Thumb sin push (`ldr; movs; movs; movs; beq`) = corrida de 3 "punteros" → `is_table()` frenaba
  la raíz aunque es destino de `bl` (0x203964). Fix: `table_all_plausible()`; un destino de bl/blx solo se frena si
  todos los valores de la corrida son punteros plausibles (sin ese límite se decodificaba la vtable 0x2e4238).
  −14 rebases, 0 nuevos, nada en rodata. La causa probable de los "congelamientos" de #075–#085 es este bucle (el
  hilo de carga a tope; el resto del proceso parado en algunas corridas).
- **#090**: rebase 52802. **Pasa el bucle** (carga `menuLogo.group.bin`). Crash en 0x1f316a (`strb [r4,#8]`, r4 =
  0x3d088889 = 1/30f): 0x1f3148 = prólogo `push {r2-r6,lr}; movs r4,r0` (0x0004b57c) rebaseado; #077 no lo
  alcanzaba porque la palabra anterior es un literal a __DATA (vecino "de dato"). Fix: `push {..,lr}` Thumb
  decodificado justo en la palabra (`CodeScan.tcode`, nuevo) = entrada de función, salvo literal de thunk
  Thumb→ARM (`bx pc; ldr ip,[pc]; ldr pc,[pc]; .word fn`, fn = 0x0003b5xx en 0x304488…, palabra previa
  `ldr pc,[pc]`). −10 rebases (los 10 prólogos 0x0004b57c restantes), 0 nuevos.
- **#091**: rebase 52792. **PRIMERA PANTALLA: llega al title** (confirmado por el usuario en la consola). Carga
  `menuSplash` y `musicMenu00`; a los 40 s: 621 frames presentados, 2625 draw calls, sin crash. Pendiente visto en
  el log: `unimpl: _AudioFileOpenWithCallbacks` + `AudioQueueNewOutput: formato` (música del menú muda) y
  `-[GLView touchesBegan/Moved/Ended:withEvent:]` sin implementar (táctil/controles).
  Diagnósticos aún activos (quitar al cerrar): hooks de patch.c (s3eFileRead, unixRead, sqlite3_step/column_text,
  s3eErrorSet, s3eFileOpen_internal, s3eMallocBase, 0x1600b8), `d_read` en darwin.c, `wd.txt` del watchdog,
  manejador de excepciones de kubridge (`exc.txt`), log de espacio libre en main.c.
- **#092** (input): ni el táctil ni los botones (que también se convierten en toques vía `controls.txt`) hacían
  nada. Fix 1 en `uikit.c`: UIView reenvía `touches*` a su superview (cadena de responders), porque GLView no
  los implementa. Corrida: congelamiento a los 14.7 s en la etapa de la base de datos, sin excepción (intermitente,
  ver #078–#085: sigue sin explicar).
- **#093**: quitado el log de `stat()` de darwin.c (cientos por segundo, ~100 ms cada uno). Llega al title a los
  16 s, 873 frames a los 40 s.
- **#094**: log de toques; el usuario prueba en el title: los toques llegan a GLView y se reenvían a TopView,
  pero los `touches*` de TopView son `bx lr` (vacíos). Los que procesan son los de **MainView** (hermana debajo
  de GLView). GLView implementa `pointInside:withEvent:` (devuelve NO para dejar pasar el toque). Fix 2:
  `hit_view` llama a `pointInside:withEvent:` cuando la clase del juego lo implementa.
- **#095**: **el input funciona**: el usuario avanza desde el title y el juego carga el menú principal
  (`menuMainHigh`, `menuMainCommon`, texturas `main/high/*`, `menuFreemium`). Crash a los 28 s: tras
  `sqlite3_step(822e1400) = 1` (SQLITE_ERROR) viene `sqlite3_step(NULL) = 21` y un Data abort en 0x1554e0
  (r0 = 0x40, puntero nulo + offset); llamadores 0x155b0b ← 0x10f9a1 ← 0x155c77. Siguiente paso: loguear el SQL y
  `sqlite3_errmsg` de la sentencia que falla (¿tabla o columna que falta en save.bin, o SQL dañado por un rebase?).
- **#096**: `p_sqlite3_step` vuelca las cadenas del Vdbe al fallar: **"cannot start a transaction within a
  transaction"**. `PORT_AUTOTAP` (port_config.h, input.c): toque automático en el centro a los N s para que el ciclo
  automático pase el title sin manos (0 en builds para jugar).
- **#097–#100**: hooks en `sqlite3_prepare` (0x286fa0), `sqlite3_exec` (0x286a60) y prepare_v2 (0x286fc0); 0x2864c0
  resultó ser `sqlite3_bind_text` (mi hook lo crasheó en #099, quitado). Cadena: el wrapper de sentencias de la app
  (ctor 0x16f27c) hace `BEGIN` si el SQL empieza con INSERT/REPLACE y prepara con 0x286fc0; `INSERT OR REPLACE INTO
  save.version …` se preparaba **OK con sentencia NULL** → `step(NULL)`, el dtor (0x16f3fc) hace `COMMIT` con
  `sqlite3_db_handle(NULL)` = NULL → la transacción queda abierta y todos los `BEGIN` siguientes fallan.
- **#101**: causa: palabras rebaseadas dentro de las tablas de SQLite (0x2c600c–0x2c9208): tokenizador
  (0x01006700, 0x01006604, 0x01004500, 0x00060400), parser (0x001d01b0, 0x003101b0, 0x00090008, 0x0011002c) y el hash
  de keywords (0x2c8d90 = 0x00275800). `sqlite_const()` reescrito: ahí solo se rebasean punteros a código/cadenas
  de la app; bytes chicos no plausibles fuera de tablas de punteros y pares u16 con la mitad baja repetida en el
  vecino se excluyen. −10 rebases, 0 nuevos; se conservan tablas de métodos, nombres de opcodes y cadenas.
- **#102**: `BEGIN`/`COMMIT` OK. Crash en 0x1554e0 (this = 0x40): 0x155b00 = `movs r1,r7; movs r0,r6` (0x00300039)
  rebaseado; la rama 0x155af6 no se decodificaba (`is_table`: pares Thumb 0x390003/0x309a00/0x30e007). Intentos de
  generalizar el freno (destinos de b/bcc, longitud, paridad, alineación) decodificaban vtables reales (0x2e4238,
  0x2d7424) o deshacían #089 → descartados. Nuevo `tools/code_roots.txt`: raíces de código confirmadas en consola,
  sin freno de tabla. Entrada 0x155af7. −1 rebase (solo 0x155b00). rebase 52781.
- **#103**: **menú principal estable**: carga menuMain*/texturas/menuFreemium y sigue vivo a los 50 s (649 frames,
  8307 draw calls). PORT_AUTOTAP vuelto a 0 para jugar.
- **#104–#107** (desbloqueo + "congelamiento"): `source/storekit.c` nuevo: StoreKit emulado (SKPaymentQueue,
  SKPayment/SKMutablePayment, SKPaymentTransaction, SKProductsRequest/Response, SKProduct). Toda compra se completa
  sin cobrar (la App Store de esta versión ya no existe, como el bypass de Rayman Jungle Run); `PORT_IAP_UNLOCK`
  (`com.konami.pes2012.product.nc.fullversion`) se entrega como "restaurada" al registrarse el observer. Las
  respuestas van por `storekit_tick()` en el run loop principal. Log: `StoreKit: transacción … -> restaurada` a 5.7 s.
  **El "congelamiento a los ~15 s" (#075–#106) era un artefacto de medición**: `hottest.py` bajaba los logs por FTP
  con el juego abierto y en exFAT el tamaño del archivo abierto no se actualiza → log y wd.txt truncados, sin dump
  porque no había crash. Fix: `hottest.py` cierra el juego (`destroy`) antes de bajar. Watchdog del heartbeat sin
  malloc/snprintf (`wd.txt`).
- **#108**: con la versión completa y autotap llega a **selección de equipos** (menuTeam4, menuTeamPES, uniformes).
  Crash: salto a 0x00170459 sin slide desde 0x1f094d: entrada de vtable 0x2e87c8 (última, hacia una hoja Thumb sin
  push; la vtable gemela 0x2e8bc0 sí estaba). La heurística la descarta sin dejarla en `report.txt`; dos ajustes de
  reglas no la alcanzaban o metían falsos (0x25f3ec) → nuevo `tools/rebase_force.txt` (palabras confirmadas en consola
  que se rebasean siempre). +1 rebase (solo 0x2e87c8). rebase 52782. Build para jugar: PORT_AUTOTAP 0, WATCHDOG 20.
  El usuario estaba probando Exhibición durante el ciclo automático: no lanzar ciclos mientras juega.
- **#109** (usuario): **llega al partido (ingame, Exhibición)**. Única falla: picos/triángulos que cruzan la pantalla
  desde los jugadores (menú de selección de equipos a veces; ingame al moverse). Sin GL faltante relevante; skinning
  por CPU (CIwAnimSkin) y FPSCR solo RunFast (0x03000000 en 0x2c1414, sin VFP short-vector). Causa: vitaGL compilado
  con `DRAW_SPEEDHACK=2` (SAFER_DRAW_SPEEDHACK): los arrays de vértices de cliente mayores a SAFE_DRAW_SIZE_THRESHOLD
  no se copian y la GPU los lee después de que el juego ya reusó su buffer de skinning para el jugador siguiente.
  Fix: vitaGL sin DRAW_SPEEDHACK (copia siempre); `libvitaGL.a` recompilada (con `$VITASDK/bin` en PATH: dentro de
  cmake fallaba por PATH). Desplegado, pendiente de prueba del usuario.
- **#110–#124** (picos en los jugadores, guiado por pruebas del usuario):
  - DRAW_SPEEDHACK de vitaGL quitado (#109) no cambió los picos.
  - **Cuelgue de los ciclos automáticos (~15 s)**: era la auto-suspensión por inactividad (nadie toca la Vita):
    `sceKernelPowerTick(DISABLE_AUTO_SUSPEND)` cada 1 s en el run loop (uikit.c). Mi diagnóstico previo de "log
    truncado por exFAT" era incompleto (igual `hottest.py` cierra el juego antes de bajar logs).
  - Medición de vértices en `glDrawElements` (#111–#117): posiciones `GL_SHORT` x3 stride 6 de memoria cliente.
    El umbral |v| > 1e4 era inválido (están cuantizadas a todo el rango short): conclusión "llegan rotos" no vale.
  - Watchpoint por software (página en solo lectura con kuKernelMemProtect + manejador kubridge que emula stores
    ajenos al buffer): escritor = constructor de bloque del asignador temporal de IwGx (0x179690), todo en el hilo
    de render (#118): no es un race.
  - Convertir short/byte desalineados a float (#119): sin efecto, revertido (needs_float solo GL_FIXED).
  - Traza GL de frames (#120–#121): IwGx usa transformación por **software** (vértices proyectados en CPU, matriz de
    textura x1/4096, proyección = rotación). TexCoordPointer(…, 0x0) con VBO.
  - **#121: 3 instrucciones ARM rebaseadas** en funciones hoja sin decodificar (push {r4-r9} sin lr) del
    rasterizado/texturizado por software: `eoreq ip, ip, r2, lsr #20` (0x002cca22) en 0xd987c/0x114730/0x114aa0 →
    `eorls`. Raíces 0xd982c/0x1146e0/0x114a50 en `tools/code_roots.txt` (−3 rebases). El usuario confirma: **las
    texturas de los jugadores ya quedan pegadas al cuerpo**; los picos siguen. Barridos generales (stmfd sin lr,
    instrucciones condicionales entre código) no encuentran más.
  - Controles (#117): `controls.txt` LSTICK → dedo virtual en el joystick (101,220 r45), CROSS = A (355,274),
    CIRCLE/SQUARE = B (437,237), START = pausa (15,15). Zona muerta del stick 0.35 (con la deriva el dedo quedaba
    apretado y bloqueaba los menús). Usuario: controles OK.
  - #122–#124: anunciar GL_OES_matrix_palette + dlsym GL: el binario no tiene esas funciones (IwGx ES1 siempre hace
    skinning/transform en CPU) → revertido.
  - **Pendiente**: picos restantes en la ruta ES1/software de IwGx. El juego está pensado para **ES2 en iPhone**
    (app.icf: SysGlesVersion=2; ES1 solo iPad2/Android) y nosotros forzamos ES1 (patch.c). Siguiente paso
    propuesto: exportar GLES2 (shaders GLSL vía vitaGL) para usar la ruta de GPU del juego.
- **#125** (picos siguen): causa hallada en PES 2011 (su corrida #6, reproducida en hosttest): la tabla de senos
  u16 de IwGeom en **0x100c37c** tenía 5 palabras rebaseadas (0x100c37c/80/84/88, 0x100c3cc) → sin(1-2°) ≈ 9,5 →
  SLERP de huesos ×7 → picos. Fix: `tools/rebase_skip.txt` (lo lee `ios_prep.py`); rebase.bin 52779 → 52774.
  Además: menú SELECT+START de opciones de video copiado de Ferrari GT (`options.c`, `font8x8.c`; `options.txt`
  reemplaza a `rotation.txt`).
- Aviso aparte (sin efecto por ahora): `Info.plist` del IPA es XML y el loader solo lee plist binario.

## PENDIENTE EN CONSOLA (corrida iOS #1)

1. Subir los datos: `python3 -I tools/deploy_data.py` (sube `ux0_data/pes2012ios/` → `ux0:data/pes2012ios/`, ~640 MB).
2. Instalar `build/pes2012ios.vpk` (TITLEID `PES201201`, convive con el Android `PES201200`).
3. Abrir el juego, traer logs y dump con `python3 -I tools/deploy_data.py --logs`. Decir si crasheó o quedó congelado.
4. Qué mirar en el log: `SysStackSwitch forced off (0x4116c)`; `EAGLContext initWithAPI:2 -> nil` seguido de
   `initWithAPI:1`; `pthread_create(...)` de los hilos s3e; `unimpl: _AudioFileOpenWithCallbacks` (música).
5. En un crash: PC − 0x98000000 = dirección en el Mach-O (`otool -tV`, tiene símbolos `_s3e*`); dirección de datos
   < 0x02000000 = rebase faltante (buscar en `ux0_data/pes2012ios/report.txt`). Stop reason `0x10006` = SP fuera del
   stack del hilo (fibras s3e, ver PORTING_PLAN §2).
