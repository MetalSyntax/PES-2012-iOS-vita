#pragma once
// Opciones de video del port (menú SELECT+START, guardadas en DATA_PATH options.txt).

enum { FILTER_BILINEAR, FILTER_SHARP, FILTER_PIXEL };
extern int opt_filter;      // escalado del drawable a la pantalla
extern int opt_msaa;        // 0 = off, 2 = 2x, 4 = 4x (se aplica al reiniciar: vitaGL lo fija en vglInit)
extern int opt_msaa_active; // el valor con que arrancó vitaGL
extern int opt_stretch;     // 1 = pantalla completa (960x544), 0 = aspecto original con barras

void options_load(void);              // antes de vglInitExtended
int options_msaa_mode(void);          // SceGxmMultisampleMode para vglInitExtended
void options_open(void);              // input.c: SELECT+START
int options_pending(void);
int options_active(void);             // menú abierto (el watchdog de uikit.c no cuenta ese tiempo)
void options_run(void (*draw_game)(void)); // gles_present: bucle modal con el juego en pausa detrás
