#!/usr/bin/env python3
"""Todos los hilos de un .psp2dmp: nombre, estado, PC/LR y direcciones del juego (Mach-O, sin slide) en la pila.

Sirve para cuelgues (el watchdog del runtime fuerza el dump; el hilo que importa no es el que "crasheó").
Usa vita-parse-core (ruta en ~/vita-tools/vita-parse-core o $VITA_PARSE_CORE).
Uso: python3 tools/dump_threads.py logs/psp2core-....psp2dmp
"""
import os, struct, sys

VPC = os.environ.get('VITA_PARSE_CORE', os.path.expanduser('~/vita-tools/vita-parse-core'))
sys.path.insert(0, VPC)
from core import CoreParser  # noqa: E402

SLIDE, LO, HI = 0x98000000, 0x98001000, 0x9831e000

core = CoreParser(sys.argv[1])
for t in core.threads:
    regs = getattr(t, 'regs', None)
    if not regs:
        continue
    g = regs.gpr
    sp, lr, pc = g[13], g[14], g[15]
    tag = lambda v: '0x%x(juego 0x%x)' % (v, v - SLIDE) if LO <= v < HI else '0x%x' % v
    print('== %s  estado=0x%x  stop=0x%x' % (t.name, t.status, t.stop_reason))
    print('   PC=%s  LR=%s  SP=0x%x' % (tag(pc), tag(lr), sp))
    print('   r0-r7: ' + ' '.join('%08x' % x for x in g[:8]))
    stack = core.read_vaddr(sp, 0x800) or b''
    found = []
    for i in range(0, len(stack) - 3, 4):
        v = struct.unpack_from('<I', stack, i)[0]
        if LO <= v < HI:
            found.append('0x%x' % (v - SLIDE))
    print('   pila(juego): ' + ' '.join(found[:16]))
