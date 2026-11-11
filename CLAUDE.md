# PES 2012 (iOS → PS Vita)

Port con **loader Mach-O + runtime Objective-C propios** (no soloader, no JNI), a partir de
`PES2012_v1.0.2_os30.ipa` (armv6, `cryptid 0`, SDK iPhoneOS 3.2). Marmalade va enlazado estáticamente en el
Mach-O (no hay `.s3e`). El port Android (Marmalade `.s3e`) es otro repo: `../Incompletes/PES-2012-vita`.

Leer `PORTING_PLAN.md` antes de tocar nada; el estado y la bitácora están en `port_progress.md`.

## Estructura
- `source/` común a los ports iOS de la familia (copiado de `../HAWX-vita/source` con
  `../HAWX-vita/tools/sync_runtime.sh .`). Propios de este juego: `source/port_config.h`, `source/patch.c`
  (SysStackSwitch off, GLES1 forzado, zlib, `pthread_attr_setstack`).
- `tools/ios_prep.py` (rebase.bin/binds.bin), `tools/nib_prep.py`, `tools/gen_cxx.py`,
  `tools/stage_data.sh` (arma `ux0_data/pes2012ios/`), `tools/deploy_data.py` (FTP, `--logs`).
- `game_app` → `pes2012ios_extract/Payload/PES2012.app` del IPA (gitignored).
- En la Vita: `ux0:data/pes2012ios/{game_app/, rebase.bin, binds.bin, nib.txt, nibs/, controls.txt, Documents/, logs/}`.

## Reglas
- ABI iOS: softfp (igual que la Vita), r9 temporal (todo con `-ffixed-r9`; llamar al juego con `call_guest`),
  pila alineada a 4 (todo lo nuestro entra por `ml_align_call`), int64 sin alinear a par de registros.
- Struct > 4 bytes se devuelve por puntero oculto (`objc_msgSend_stret`): la IMP nativa recibe `(ret*, self, _cmd, ...)`.
- Crash: PC − 0x98000000 = dirección en el Mach-O (tiene símbolos `_s3e*`: `otool -tV game_app/PES2012`).
  Dirección de datos < 0x02000000 = rebase faltante (`report.txt`).
- Lo aprendido de Marmalade está en `../marmalade-vita-core` y `../Incompletes/PES-2012-vita` (Bug #18); ver PORTING_PLAN §2 qué aplica.
- Nada del IPA ni derivados al repo. Un bug por corrida de consola, guiado por el log. Nunca Vita3K.
- Build: `export VITASDK=$HOME/vitasdk; cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug && make -C build`.
