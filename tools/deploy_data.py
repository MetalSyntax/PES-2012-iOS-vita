#!/usr/bin/env python3
"""Sincroniza ux0_data/<slug>/ con ux0:data/<slug>/ de la Vita por FTP (VitaShell, puerto 1337).

- Sube solo lo que falta o cambió de tamaño. En Documents/ (partidas) y logs/ nunca se sube ni
  se borra nada.
- --logs: baja los logs/<slug>_NNN.log nuevos o que cambiaron y el .psp2dmp más reciente de ux0:data/ a logs/.
- --archive: mueve lo que haya en logs/ a logs/log/ (*.log) y logs/dmp/ (dumps y sus análisis); --logs no
  vuelve a bajar lo archivado.
- IP y slug salen de .psvita-toolkit.json (vita_ip, slug); --ip los pisa.

Uso: python3 -I tools/deploy_data.py [--ip 192.168.x.y] [--logs] [--dry-run]
"""
import ftplib, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, '.psvita-toolkit.json')))
SLUG = CFG['slug']
LOCAL = os.path.join(ROOT, 'ux0_data', SLUG)
REMOTE = '/ux0:/data/' + SLUG
NO_PUSH = {'Documents', 'logs'}
# generados por tools/*.py: al regenerarlos suelen conservar el tamaño, así que se suben siempre
ALWAYS = {'rebase.bin', 'binds.bin', 'nib.txt'}


def ls(ftp, path):
    out = {}
    try:  # el FTP de VitaShell ignora el argumento de LIST: hay que entrar al directorio
        ftp.cwd(path)
        lines = []
        ftp.retrlines('LIST', lines.append)
    except ftplib.error_perm:
        return None
    for l in lines:
        p = l.split(None, 8)
        if len(p) == 9 and p[8] not in ('.', '..'):
            out[p[8]] = (l[0] == 'd', int(p[4]))
    return out


def push(ftp, local, remote, dry):
    have = ls(ftp, remote)
    if have is None:
        print('  mkdir', remote)
        if not dry:
            ftp.mkd(remote)
        have = {}
    for n in sorted(os.listdir(local)):
        if n.startswith('._') or n == '.DS_Store':
            continue
        lp, rp = os.path.join(local, n), remote + '/' + n
        if os.path.isdir(lp):
            if n not in NO_PUSH or remote != REMOTE:
                push(ftp, lp, rp, dry)
        elif n in ALWAYS or n not in have or have[n][1] != os.path.getsize(lp):
            print('  put', rp, os.path.getsize(lp))
            if not dry:
                with open(lp, 'rb') as f:
                    ftp.storbinary('STOR ' + rp, f)


LOGS = os.path.join(ROOT, 'logs')


def archive():
    for n in os.listdir(LOGS):
        if os.path.isfile(os.path.join(LOGS, n)):
            sub = os.path.join(LOGS, 'log' if n.endswith('.log') else 'dmp')
            os.makedirs(sub, exist_ok=True)
            os.replace(os.path.join(LOGS, n), os.path.join(sub, n))


def pull_logs(ftp):
    dst = LOGS
    os.makedirs(dst, exist_ok=True)
    for n, (d, sz) in (ls(ftp, REMOTE + '/logs') or {}).items():
        lp = os.path.join(dst, n)
        old = os.path.join(dst, 'log', n)
        if os.path.exists(old) and os.path.getsize(old) == sz:
            continue
        if not d and (not os.path.exists(lp) or os.path.getsize(lp) != sz):
            print('  get', n)
            with open(lp, 'wb') as f:
                ftp.retrbinary('RETR %s/logs/%s' % (REMOTE, n), f.write)
    # solo el dump más reciente (psp2core-<timestamp>-...), y solo si no está ya (con o sin prefijo <slug>-)
    dumps = [n for n, (d, sz) in (ls(ftp, '/ux0:/data') or {}).items() if n.endswith('.psp2dmp') and not d]
    stamp = lambda n: int(n.split('-')[1]) if n.split('-')[1].isdigit() else 0
    if dumps:
        n = max(dumps, key=stamp)
        have = os.listdir(dst) + (os.listdir(os.path.join(dst, 'dmp')) if os.path.isdir(os.path.join(dst, 'dmp')) else [])
        if not any(f.endswith(n) for f in have):
            print('  get', n)
            with open(os.path.join(dst, n), 'wb') as f:
                ftp.retrbinary('RETR /ux0:/data/' + n, f.write)


def main():
    a = sys.argv[1:]
    if '--archive' in a:
        return archive()
    ip = a[a.index('--ip') + 1] if '--ip' in a else CFG.get('vita_ip')
    ftp = ftplib.FTP()
    ftp.connect(ip, CFG.get('vita_port', 1337), timeout=20)
    ftp.login()
    if '--logs' in a:
        pull_logs(ftp)
    else:
        push(ftp, LOCAL, REMOTE, '--dry-run' in a)
    ftp.quit()


if __name__ == '__main__':
    main()
