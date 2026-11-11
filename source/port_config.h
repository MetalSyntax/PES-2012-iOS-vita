#pragma once
// Configuración propia de este juego (el resto de source/ es común a los ports iOS de la familia;
// se sincroniza con tools/sync_runtime.sh desde HAWX-vita).
#define PORT_NAME "PES 2012"
#define PORT_SLUG "pes2012ios"
#define PORT_EXEC "PES2012"            // CFBundleExecutable
#define PORT_ROTATION 1                // drawable vertical -> 1 landscape-right, 2 landscape-left, 0 vertical
#define PORT_STRETCH 0                 // 0 = conservar aspecto (816x544 con barras), 1 = estirar a 960x544
#define PORT_SYSTEM_VERSION "3.1.3"    // [UIDevice systemVersion] (MinimumOSVersion 3.0, SDK 3.2; sin orientación en Info.plist, como PES 2011)
#define PORT_DEVICE_MACHINE "iPhone2,1" // hw.machine (3GS)
#define GLES_POOL_MB 24
#define PORT_ACCEL_FROM_STICK 0        // por confirmar si el juego usa acelerómetro
