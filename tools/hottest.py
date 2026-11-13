#!/usr/bin/env python3
"""Una vuelta de prueba en consola sin tocar la Vita: cerrar -> subir -> lanzar -> esperar -> traer log/dump.

Requiere vitacompanion (puerto 1338: launch/destroy) y su FTP o el de VitaShell (1337).
Uso: python3 -I tools/hottest.py [segundos=30] [--no-deploy]
Imprime el final del log nuevo y, si hubo crash, el resumen del dump (psvita-toolkit analyze).
"""
import json, os, socket, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, '.psvita-toolkit.json')))
IP, TID, SLUG = CFG['vita_ip'], CFG['titleid'], CFG['slug']
LOGS = os.path.join(ROOT, 'logs')


def vc(cmd):
    with socket.create_connection((IP, 1338), timeout=10) as s:
        s.sendall((cmd + '\n').encode())
        s.settimeout(3)
        try:
            return s.recv(4096).decode(errors='replace').strip()
        except socket.timeout:
            return ''


def run(*a):
    return subprocess.run(a, cwd=ROOT, capture_output=True, text=True)


def listing():
    return set(os.listdir(LOGS)) if os.path.isdir(LOGS) else set()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    secs = int(args[0]) if args else 30
    vc('destroy')
    time.sleep(2)
    if '--no-deploy' not in sys.argv:
        r = run('psvita-toolkit', 'deploy', '--eboot', '--yes')
        if r.returncode:
            sys.exit('deploy del eboot falló:\n' + r.stdout[-800:] + r.stderr[-800:])
        r = run(sys.executable, '-I', 'tools/deploy_data.py')
        print(''.join(l + '\n' for l in r.stdout.splitlines() if 'put' in l and 'nib' not in l and 'binds' not in l), end='')
    run(sys.executable, '-I', 'tools/deploy_data.py', '--archive')  # logs/ queda solo con esta corrida
    before = listing()
    print('launch', TID, '->', vc('launch ' + TID) or 'ok')
    time.sleep(secs)
    # cerrar ANTES de bajar: con el archivo abierto, el tamaño en el directorio (exFAT) queda viejo y el FTP baja
    # el log truncado (parecía un congelamiento a los ~15 s sin dump; #075-#106)
    vc('destroy')
    time.sleep(3)
    run(sys.executable, '-I', 'tools/deploy_data.py', '--logs')
    new = sorted(listing() - before)
    logs = sorted((f for f in listing() if f.startswith(SLUG + '_') and f.endswith('.log')),
                  key=lambda f: os.path.getmtime(os.path.join(LOGS, f)))
    if logs:
        log = os.path.join(LOGS, logs[-1])
        print('== %s' % log)
        lines = open(log, errors='replace').read().splitlines()
        tail = lines[next((i for i, l in enumerate(lines) if 'run loop principal' in l), max(0, len(lines) - 40)):]
        print('\n'.join(tail[-60:]))
    dumps = [f for f in new if f.endswith('.psp2dmp') and not f.startswith('._')]
    for d in dumps:
        r = run('psvita-toolkit', 'analyze', os.path.join(LOGS, d))
        out = r.stdout.splitlines()
        keep = [l for l in out if any(k in l for k in ('HILO EN CRASH', 'Razón', 'PC:', 'LR:', ' R0 ', ' R1 ', ' R2 ', ' R3 ', ' R4 ', ' R5 ', ' R6 ', ' R7 ', 'SP :'))]
        print('== CRASH %s' % d)
        print('\n'.join(keep))
    if not dumps:
        print('== sin dump nuevo (sigue corriendo, se colgó o salió solo)')
    vc('destroy')


if __name__ == '__main__':
    main()
