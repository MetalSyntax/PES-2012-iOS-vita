// Touch frontal + botones de la Vita -> toques de UIKit (uikit.c) en coordenadas de la vista.
//
// La pantalla muestra el drawable girado/escalado (gles_present); acá se hace el mapeo inverso:
// punto de pantalla -> normalizado en el rectángulo destino -> coordenadas de la vista de iOS.
// Botones: cada uno "toca" un punto configurable en ux0:data/<slug>/controls.txt, en coordenadas
// lógicas de lo que se ve (p. ej. 480x320 para un juego apaisado):
//     CROSS 420 260
//     RTRIGGER 440 160
// Se edita por FTP sin recompilar. SELECT+START (2 s) cambia la rotación y la guarda.
#include <psp2/ctrl.h>
#include <psp2/kernel/processmgr.h>
#include <psp2/touch.h>
#include <stdio.h>
#include <string.h>

#include "gles.h"
#include "hle.h"
#include "input.h"
#include "log.h"
#include "options.h"

#define FINGERS 6
#define BTN_FINGER0 2 // los botones usan dedos virtuales 2..7 (los reales, 0..1)

static struct {
    int id, down;
} fingers[2];
static uint32_t prev_buttons;
static float stick_x, stick_y;

static const struct {
    const char *name;
    uint32_t mask;
} names[] = {
    {"CROSS", SCE_CTRL_CROSS},       {"CIRCLE", SCE_CTRL_CIRCLE}, {"SQUARE", SCE_CTRL_SQUARE},
    {"TRIANGLE", SCE_CTRL_TRIANGLE}, {"LTRIGGER", SCE_CTRL_LTRIGGER}, {"RTRIGGER", SCE_CTRL_RTRIGGER},
    {"START", SCE_CTRL_START},       {"SELECT", SCE_CTRL_SELECT}, {"UP", SCE_CTRL_UP},
    {"DOWN", SCE_CTRL_DOWN},         {"LEFT", SCE_CTRL_LEFT},     {"RIGHT", SCE_CTRL_RIGHT},
};
#define MAX_BIND 6
static struct {
    uint32_t mask;
    float x, y; // lógicas (lo que se ve)
} binds[MAX_BIND];
static int nbinds;
static float lstick[3]; // LSTICK x y radio: el stick arrastra un dedo virtual sobre el joystick de la pantalla
#define STICK_FINGER 6

static void load_controls(void) {
    FILE *f = fopen(DATA_PATH "controls.txt", "r");
    if (!f) {
        l_info("sin controls.txt: solo touch + stick (acelerómetro)");
        return;
    }
    char line[96], n[32];
    float x, y;
    while (fgets(line, sizeof(line), f) && nbinds < MAX_BIND)
        if (line[0] != '#' && sscanf(line, "LSTICK %f %f %f", &lstick[0], &lstick[1], &lstick[2]) == 3)
            l_info("controls.txt: stick izquierdo -> joystick en (%g, %g) r %g", lstick[0], lstick[1], lstick[2]);
        else if (line[0] != '#' && sscanf(line, "%31s %f %f", n, &x, &y) == 3)
            for (unsigned i = 0; i < sizeof(names) / sizeof(names[0]); i++)
                if (!strcmp(n, names[i].name))
                    binds[nbinds++] = (typeof(binds[0])){names[i].mask, x, y};
    fclose(f);
    l_info("controls.txt: %d botones", nbinds);
}

void input_init(void) {
    sceTouchSetSamplingState(SCE_TOUCH_PORT_FRONT, SCE_TOUCH_SAMPLING_STATE_START);
    sceCtrlSetSamplingMode(SCE_CTRL_MODE_ANALOG);
    load_controls();
}

void input_stick(float *x, float *y) {
    *x = stick_x;
    *y = stick_y;
}

// Normalizado [0,1] en lo que se ve -> coordenadas de la vista (ver uv_rot en gles.c).
static void to_view(float nx, float ny, float *vx, float *vy) {
    switch (gles_rotation()) {
    case 1:
        *vx = (1 - ny) * fb_w, *vy = nx * fb_h;
        break;
    case 2:
        *vx = ny * fb_w, *vy = (1 - nx) * fb_h;
        break;
    default:
        *vx = nx * fb_w, *vy = ny * fb_h;
    }
}

static int screen_to_view(int sx, int sy, float *vx, float *vy) {
    int dx, dy, dw, dh;
    gles_dest_rect(&dx, &dy, &dw, &dh);
    float nx = (sx - dx) / (float)dw, ny = (sy - dy) / (float)dh;
    if (nx < 0 || nx > 1 || ny < 0 || ny > 1)
        return 0;
    to_view(nx, ny, vx, vy);
    return 1;
}

static void logical_size(float *w, float *h) {
    *w = gles_rotation() ? fb_h : fb_w;
    *h = gles_rotation() ? fb_w : fb_h;
}

void input_poll(void) {
    SceTouchData t;
    if (sceTouchPeek(SCE_TOUCH_PORT_FRONT, &t, 1) < 1)
        t.reportNum = 0;
    // dos dedos como máximo (los juegos de 2009 rara vez usan más)
    for (int k = 0; k < 2; k++) {
        int found = -1;
        for (int i = 0; i < (int)t.reportNum; i++)
            if (fingers[k].down && t.report[i].id == fingers[k].id)
                found = i;
        if (fingers[k].down && found < 0) {
            fingers[k].down = 0;
            ui_touch_event(k, 3, -1, -1);
        }
    }
    for (int i = 0; i < (int)t.reportNum && i < 2; i++) {
        float vx, vy;
        if (!screen_to_view(t.report[i].x / 2, t.report[i].y / 2, &vx, &vy))
            continue;
        int k = -1;
        for (int j = 0; j < 2; j++)
            if (fingers[j].down && fingers[j].id == t.report[i].id)
                k = j;
        if (k < 0) {
            for (int j = 0; j < 2 && k < 0; j++)
                if (!fingers[j].down)
                    k = j;
            if (k < 0)
                continue;
            fingers[k].down = 1;
            fingers[k].id = t.report[i].id;
            ui_touch_event(k, 0, vx, vy);
        } else {
            ui_touch_event(k, 1, vx, vy);
        }
    }

#if PORT_AUTOTAP
    { // ciclo automático sin manos: un toque en el centro para pasar el title
        static int step;
        if (step < 2 && sceKernelGetProcessTimeWide() > PORT_AUTOTAP * 1000000ULL + step * 100000ULL) {
            l_info("autotap %s", step ? "fin" : "inicio");
            ui_touch_event(7, step ? 3 : 0, fb_w / 2.0f, fb_h / 2.0f);
            step++;
        }
    }
#endif
    SceCtrlData pad;
    sceCtrlPeekBufferPositive(0, &pad, 1);
    stick_x = (pad.lx - 128) / 127.0f;
    stick_y = (pad.ly - 128) / 127.0f;
    if (stick_x > -0.12f && stick_x < 0.12f)
        stick_x = 0;
    if (stick_y > -0.12f && stick_y < 0.12f)
        stick_y = 0;
    if (lstick[2] > 0) { // dedo virtual: toca el centro y se mueve con la inclinación; suelta al volver al centro
        static int down;
        float lw, lh, vx, vy;
        logical_size(&lw, &lh);
        // zona muerta propia: con la deriva del stick el dedo quedaba apretado y el juego (un solo toque) ignoraba el resto
        int active = stick_x * stick_x + stick_y * stick_y > 0.35f * 0.35f;
        to_view((lstick[0] + stick_x * lstick[2]) / lw, (lstick[1] + stick_y * lstick[2]) / lh, &vx, &vy);
        if (active && !down) {
            float cx, cy;
            to_view(lstick[0] / lw, lstick[1] / lh, &cx, &cy);
            ui_touch_event(STICK_FINGER, 0, cx, cy);
            down = 1;
        }
        if (active)
            ui_touch_event(STICK_FINGER, 1, vx, vy);
        else if (down) {
            ui_touch_event(STICK_FINGER, 3, -1, -1);
            down = 0;
        }
    }
    uint32_t b = pad.buttons, changed = b ^ prev_buttons;
    for (int i = 0; i < nbinds; i++) {
        if (!(changed & binds[i].mask))
            continue;
        float lw, lh, vx, vy;
        logical_size(&lw, &lh);
        to_view(binds[i].x / lw, binds[i].y / lh, &vx, &vy);
        ui_touch_event(BTN_FINGER0 + i, (b & binds[i].mask) ? 0 : 3, vx, vy);
    }
    // SELECT+START: menú de opciones de video (options.c; ahí también va la rotación)
    uint32_t both = SCE_CTRL_SELECT | SCE_CTRL_START;
    if ((b & both) == both && (prev_buttons & both) != both)
        options_open();
    prev_buttons = b;
}
