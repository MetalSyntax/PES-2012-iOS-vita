#!/usr/bin/env python3
"""Prepara un ejecutable iOS 32 bits (Mach-O MH_EXECUTE, ARMv6/ARMv7) para el loader de la Vita.

Genera dos tablas que el loader lee desde ux0:data/<slug>/:

  rebase.bin  'RBS1' u32 count, count * u32 vmaddr   palabras con una dirección absoluta (+slide)
  binds.bin   'BND1' u32 count, count * {u32 vmaddr, u32 addend, u8 lazy, char name[] NUL}
              slots que apuntan a un símbolo externo (frameworks de Apple)

Dos casos:
  * PIE / LC_DYLD_INFO (iOS 4.3+, ej. Rayman Fiesta Run): rebase y binds exactos, de los opcodes.
  * Clásico sin relocs locales (iPhone OS 2.x/3.x, -mdynamic-no-pic, ej. Gameloft 2009-2010):
    el código tiene direcciones absolutas en los literal pools y NO hay relocs. Se reconstruyen:
      - exacto: __mod_init_func, __cfstring, __objc_* (recorrido estructural de clases,
        metaclases, ro, listas de métodos/ivars/protocolos/propiedades, categorías),
        __nl_symbol_ptr locales, __symbol_stub4, relocs locales si las hay.
      - código: descenso recursivo ARM/Thumb con capstone desde main/start, IMPs de ObjC,
        __mod_init_func y símbolos; cada `ldr rX, [pc, #imm]` alcanzado define un literal; si su
        valor cae en la imagen y el uso no es aritmético, es puntero. Tablas `ldr pc,[pc,rX,lsl#2]`.
      - datos genéricos: palabra que apunta a __DATA, a un inicio de cadena o a un inicio de
        función conocido (o una corrida >=2 de ellas: vtables).
    Lo dudoso va a report.txt para el triage en consola.

Uso: python3 -I ios_prep.py <ejecutable> <outdir> [--arch armv6|armv7]
"""
import bisect, os, struct, sys
from collections import defaultdict

LC_SEGMENT, LC_SYMTAB, LC_DYSYMTAB, LC_UNIXTHREAD = 0x1, 0x2, 0xB, 0x5
LC_DYLD_INFO, LC_DYLD_INFO_ONLY, LC_MAIN = 0x22, 0x80000022, 0x80000028
LC_FUNCTION_STARTS, LC_DATA_IN_CODE = 0x26, 0x29
S_ZEROFILL, S_NON_LAZY, S_LAZY, S_STUBS, S_GB_ZEROFILL = 0x1, 0x6, 0x7, 0x8, 0xC
INDIRECT_LOCAL, INDIRECT_ABS = 0x80000000, 0x40000000
N_STAB, N_TYPE, N_SECT = 0xE0, 0x0E, 0x0E
N_ARM_THUMB_DEF = 0x0008

# Nombres de imports que son datos (variables/clases), no funciones.
DATA_PREFIXES = ('_OBJC_CLASS_$_', '_OBJC_METACLASS_$_', '_OBJC_EHTYPE_', '_kCF', '_kEAGL', '_kAudio',
                 '_kCG', '_kSC', '_kUT', '_kSec', '_kAB', '_kCT', '_kCV', '_UIApplication', '_UIKeyboard',
                 '_UITextField', '_UIDevice', '_UIScreen', '_NSDefault', '_NSRunLoop', '_NSLocale',
                 '_NSError', '_NSURL', '_NSFile', '_NSUnderlying', '_NSInvalid', '_NSGeneric', '_NSFound',
                 '_MPMedia', '_MPMovie', '_MPMusic', '_GK', '_AVAudio', '_AVMedia', '_AVLayer',
                 '___CFConstantStringClassReference', '___stderrp', '___stdoutp', '___stdinp',
                 '__DefaultRuneLocale', '___stack_chk_guard', '__objc_empty', '___sF', '_NX',
                 '_environ', '_optarg', '_optind', '_mach_task_self_', '_vm_page_size', '_errno',
                 '_CGPointZero', '_CGRectZero', '_CGSizeZero', '_CGAffineTransformIdentity',
                 '_CGRectInfinite', '_CGRectNull', '__ZTV', '__ZTI', '__ZTS', '__ZNSs4_Rep20_S_empty',
                 '__ZSt4cout', '__ZSt4cerr', '__ZSt4endl', '_UIEdgeInsetsZero', '_NSZeroRect')


def is_data_name(n):
    return n.startswith(DATA_PREFIXES) or (n.endswith('Key') or n.endswith('Notification')) and not n.startswith('_obj')


class MachO:
    def __init__(self, path, want=None):
        raw = open(path, 'rb').read()
        off = 0
        if raw[:4] in (b'\xca\xfe\xba\xbe',):
            n = struct.unpack_from('>I', raw, 4)[0]
            arches = [struct.unpack_from('>5I', raw, 8 + i * 20) for i in range(n)]
            pick = None
            for cpu, sub, o, sz, al in arches:
                name = {6: 'armv6', 9: 'armv7', 11: 'armv7s'}.get(sub)
                if cpu == 12 and (want == name or (want is None and name == 'armv7')):
                    pick = (o, sz)
            if not pick:
                pick = (arches[0][2], arches[0][3])
            off = pick[0]
            raw = raw[pick[0]:pick[0] + pick[1]]
        self.d = raw
        self.slice_off = off
        magic, cpu, self.sub, ftype, ncmds, _sz, self.flags = struct.unpack_from('<7I', raw, 0)
        assert magic == 0xFEEDFACE and ftype == 2, 'se espera MH_EXECUTE de 32 bits'
        self.pie = bool(self.flags & 0x200000)
        self.segs, self.sects = [], []
        self.symtab = self.dysym = self.dyldinfo = None
        self.entry = None
        self.func_starts = None
        self.data_in_code = []
        o = 28
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<2I', raw, o)
            if cmd == LC_SEGMENT:
                name = raw[o + 8:o + 24].rstrip(b'\0').decode()
                vm, vs, fo, fs, mp, ip, ns, fl = struct.unpack_from('<8I', raw, o + 24)
                self.segs.append(dict(name=name, vm=vm, vs=vs, fo=fo, fs=fs, prot=ip))
                for i in range(ns):
                    so = o + 56 + i * 68
                    sn = raw[so:so + 16].rstrip(b'\0').decode()
                    sg = raw[so + 16:so + 32].rstrip(b'\0').decode()
                    a, s, of, al, ro, nr, f, r1, r2 = struct.unpack_from('<9I', raw, so + 32)
                    self.sects.append(dict(name=sn, seg=sg, addr=a, size=s, off=of, flags=f, r1=r1, r2=r2,
                                           reloff=ro, nreloc=nr))
            elif cmd == LC_SYMTAB:
                self.symtab = struct.unpack_from('<4I', raw, o + 8)
            elif cmd == LC_DYSYMTAB:
                self.dysym = struct.unpack_from('<18I', raw, o + 8)
            elif cmd in (LC_DYLD_INFO, LC_DYLD_INFO_ONLY):
                self.dyldinfo = struct.unpack_from('<10I', raw, o + 8)
            elif cmd == LC_UNIXTHREAD:
                regs = struct.unpack_from('<17I', raw, o + 16)
                self.entry = regs[15]
            elif cmd == LC_FUNCTION_STARTS:
                fo, fs = struct.unpack_from('<2I', raw, o + 8)
                self.func_starts = self._uleb_deltas(fo, fs)
            elif cmd == LC_DATA_IN_CODE:
                fo, fs = struct.unpack_from('<2I', raw, o + 8)
                for i in range(fs // 8):
                    so, ln, kind = struct.unpack_from('<IHH', raw, fo + i * 8)
                    self.data_in_code.append((so, ln, kind))
            o += size
        self.text = next(s for s in self.segs if s['name'] == '__TEXT')
        self.lo = min(s['vm'] for s in self.segs if s['name'] not in ('__PAGEZERO', '__LINKEDIT'))
        self.hi = max(s['vm'] + s['vs'] for s in self.segs if s['name'] not in ('__PAGEZERO', '__LINKEDIT'))
        self.syms = []
        if self.symtab:
            so, ns, sto, _ = self.symtab
            for i in range(ns):
                strx, typ, sect, desc, val = struct.unpack_from('<IBBHI', raw, so + i * 12)
                end = raw.index(b'\0', sto + strx)
                self.syms.append((raw[sto + strx:end].decode('latin1'), typ, sect, desc, val))

    def _uleb_deltas(self, fo, fs):
        out, addr, p, end = [], self.text_vm_guess(), fo, fo + fs
        while p < end:
            v, sh = 0, 0
            while True:
                b = self.d[p]; p += 1
                v |= (b & 0x7F) << sh; sh += 7
                if not b & 0x80:
                    break
            if v == 0:
                break
            addr += v
            out.append(addr)
        return out

    def text_vm_guess(self):
        for s in self.segs:
            if s['name'] == '__TEXT':
                return s['vm']
        return 0

    def sect(self, name, seg=None):
        for s in self.sects:
            if s['name'] == name and (seg is None or s['seg'] == seg):
                return s
        return None

    def file_off(self, vm):
        for s in self.segs:
            if s['vm'] <= vm < s['vm'] + s['fs']:
                return s['fo'] + vm - s['vm']
        return None

    def u32(self, vm):
        fo = self.file_off(vm)
        if fo is None or fo + 4 > len(self.d):
            return 0  # zerofill
        return struct.unpack_from('<I', self.d, fo)[0]

    def cstr(self, vm):
        fo = self.file_off(vm)
        if fo is None:
            return ''
        return self.d[fo:self.d.index(b'\0', fo)].decode('latin1')

    def section_of(self, vm):
        for s in self.sects:
            if s['addr'] <= vm < s['addr'] + s['size']:
                return s
        return None


def read_uleb(d, p):
    v, sh = 0, 0
    while True:
        b = d[p]; p += 1
        v |= (b & 0x7F) << sh; sh += 7
        if not b & 0x80:
            return v, p


def read_sleb(d, p):
    v, sh = 0, 0
    while True:
        b = d[p]; p += 1
        v |= (b & 0x7F) << sh; sh += 7
        if not b & 0x80:
            if b & 0x40:
                v -= 1 << sh
            return v, p


# ------------------------------------------------------------------------------------- PIE

def dyld_info_tables(m):
    rb_off, rb_sz, bd_off, bd_sz, wk_off, wk_sz, lz_off, lz_sz, _, _ = m.dyldinfo
    segs = [s for s in m.segs]
    d = m.d
    rebases = []
    p, end, seg, off, typ = rb_off, rb_off + rb_sz, 0, 0, 1
    while p < end:
        b = d[p]; p += 1
        op, imm = b & 0xF0, b & 0x0F
        if op == 0x00:
            break
        elif op == 0x10:
            typ = imm
        elif op == 0x20:
            seg = imm; off, p = read_uleb(d, p)
        elif op == 0x30:
            v, p = read_uleb(d, p); off += v
        elif op == 0x40:
            off += imm * 4
        elif op == 0x50:
            for _ in range(imm):
                rebases.append(segs[seg]['vm'] + off); off += 4
        elif op == 0x60:
            n, p = read_uleb(d, p)
            for _ in range(n):
                rebases.append(segs[seg]['vm'] + off); off += 4
        elif op == 0x70:
            rebases.append(segs[seg]['vm'] + off)
            v, p = read_uleb(d, p); off += 4 + v
        elif op == 0x80:
            n, p = read_uleb(d, p); skip, p = read_uleb(d, p)
            for _ in range(n):
                rebases.append(segs[seg]['vm'] + off); off += 4 + skip
        else:
            raise ValueError('rebase opcode %x' % op)

    binds = []

    def run_binds(start, size, lazy):
        p, end = start, start + size
        seg = off = addend = 0
        name = ''
        while p < end:
            b = d[p]; p += 1
            op, imm = b & 0xF0, b & 0x0F
            if op == 0x00:
                if lazy:
                    continue
                break
            elif op in (0x10, 0x30):
                pass
            elif op == 0x20:
                _, p = read_uleb(d, p)
            elif op == 0x40:
                e = d.index(b'\0', p)
                name = d[p:e].decode('latin1'); p = e + 1
            elif op == 0x50:
                pass
            elif op == 0x60:
                addend, p = read_sleb(d, p)
            elif op == 0x70:
                seg = imm; off, p = read_uleb(d, p)
            elif op == 0x80:
                v, p = read_uleb(d, p); off = (off + v) & 0xFFFFFFFF
            elif op == 0x90:
                binds.append((segs[seg]['vm'] + off, addend, lazy, name)); off += 4
            elif op == 0xA0:
                binds.append((segs[seg]['vm'] + off, addend, lazy, name))
                v, p = read_uleb(d, p); off = (off + 4 + v) & 0xFFFFFFFF
            elif op == 0xB0:
                binds.append((segs[seg]['vm'] + off, addend, lazy, name)); off += 4 + imm * 4
            elif op == 0xC0:
                n, p = read_uleb(d, p); skip, p = read_uleb(d, p)
                for _ in range(n):
                    binds.append((segs[seg]['vm'] + off, addend, lazy, name)); off += 4 + skip
            else:
                raise ValueError('bind opcode %x' % op)

    run_binds(bd_off, bd_sz, False)
    run_binds(wk_off, wk_sz, False)
    run_binds(lz_off, lz_sz, True)
    return rebases, binds


# ------------------------------------------------------------------------------- clásico

def local_or_ext(m, idx, addr, addend, lazy):
    # Símbolo definido en el propio binario (weak/coalesced de C++): no es import. Se emite como
    # bind con nombre '' y addend = dirección absoluta: el loader escribe addend + slide.
    name, typ, sect, desc, val = m.syms[idx]
    if not typ & N_STAB and (typ & N_TYPE) == N_SECT:
        return (addr, (val | (1 if desc & N_ARM_THUMB_DEF else 0)) + addend, lazy, '')
    return (addr, addend, lazy, name)


def classic_binds(m):
    """Imports por la tabla indirecta y relocs externas. Devuelve (binds, rebases_exactos)."""
    binds, rebases = [], set()
    d = m.d
    iso = m.dysym[12]  # indirectsymoff
    for s in m.sects:
        t = s['flags'] & 0xFF
        if t in (S_LAZY, S_NON_LAZY):
            for j in range(s['size'] // 4):
                idx = struct.unpack_from('<I', d, iso + (s['r1'] + j) * 4)[0]
                a = s['addr'] + j * 4
                if idx & INDIRECT_LOCAL:
                    if not idx & INDIRECT_ABS:
                        rebases.add(a)
                    continue
                if idx & INDIRECT_ABS:
                    continue
                binds.append(local_or_ext(m, idx, a, 0, t == S_LAZY))
        elif t == S_STUBS and s['r2'] in (8, 12) and s['seg'] == '__TEXT' and m.u32(s['addr']) in (0xE51FF004, 0xE59FC000):
            # __symbol_stub4 (no PIC): ldr pc, [pc, #-4]; .long lazy_ptr  |  ldr ip, [pc]; ldr pc, [ip]; .long lazy_ptr
            n = s['r2']
            for j in range(s['size'] // n):
                rebases.add(s['addr'] + j * n + n - 4)
    extreloff, nextrel, locreloff, nlocrel = m.dysym[14], m.dysym[15], m.dysym[16], m.dysym[17]
    for i in range(nextrel):
        addr, info = struct.unpack_from('<iI', d, extreloff + i * 8)
        symnum, pcrel, length, ext, typ = info & 0xFFFFFF, (info >> 24) & 1, (info >> 25) & 3, (info >> 27) & 1, info >> 28
        assert ext and length == 2 and not pcrel and typ == 0, 'reloc externa rara en 0x%x' % addr
        b = local_or_ext(m, symnum, addr, m.u32(addr), False)
        if not b[3]:  # símbolo propio (weak): la palabra ya trae val + addend; sumar val la duplicaba
            b = (addr, m.u32(addr), False, '')
        binds.append(b)
    for i in range(nlocrel):
        addr, info = struct.unpack_from('<II', d, locreloff + i * 8)
        if addr & 0x80000000:  # scattered: r_address = 24 bits bajos
            rebases.add(addr & 0xFFFFFF)
        else:
            rebases.add(addr)
    return binds, rebases


def objc_walk(m, bound):
    """Recorre la metadata ObjC 2. Devuelve (punteros exactos, IMPs)."""
    ptrs, imps, seen = set(), set(), set()

    def P(a):  # campo puntero en a
        if a in bound:
            return m.u32(a) if False else None
        v = m.u32(a)
        if v and m.lo <= v < m.hi:
            ptrs.add(a)
            return v
        return None

    def mlist(a):
        if not a or a in seen:
            return
        seen.add(a)
        ent = m.u32(a) & ~3
        n = m.u32(a + 4)
        for i in range(n):
            e = a + 8 + i * ent
            P(e); P(e + 4)
            imp = P(e + 8)
            if imp:
                imps.add(imp)

    def ivlist(a):
        if not a or a in seen:
            return
        seen.add(a)
        ent, n = m.u32(a), m.u32(a + 4)
        for i in range(n):
            e = a + 8 + i * ent
            P(e); P(e + 4); P(e + 8)

    def plist(a):
        if not a or a in seen:
            return
        seen.add(a)
        ent, n = m.u32(a), m.u32(a + 4)
        for i in range(n):
            P(a + 8 + i * ent); P(a + 12 + i * ent)

    def protos(a):
        if not a or a in seen:
            return
        seen.add(a)
        n = m.u32(a)
        for i in range(n):
            proto(P(a + 4 + i * 4))

    def proto(a):
        if not a or a in seen:
            return
        seen.add(a)
        P(a)  # isa
        P(a + 4)  # name
        protos(P(a + 8))
        for k in (12, 16, 20, 24):
            mlist(P(a + k))
        plist(P(a + 28))

    def ro(a):
        if not a or a in seen:
            return
        seen.add(a)
        P(a + 12)  # ivarLayout
        P(a + 16)  # name
        mlist(P(a + 20))
        protos(P(a + 24))
        ivlist(P(a + 28))
        P(a + 32)  # weakIvarLayout
        plist(P(a + 36))

    def cls(a):
        if not a or a in seen:
            return
        seen.add(a)
        isa = P(a)
        sup = P(a + 4)
        P(a + 8); P(a + 12)
        ro(P(a + 16) and (m.u32(a + 16) & ~3))
        if isa:
            cls(isa)
        if sup:
            cls(sup)

    for name in ('__objc_classlist', '__objc_nlclslist'):
        s = m.sect(name)
        if s:
            for i in range(s['size'] // 4):
                cls(P(s['addr'] + i * 4))
    for name in ('__objc_catlist', '__objc_nlcatlist'):
        s = m.sect(name)
        if s:
            for i in range(s['size'] // 4):
                c = P(s['addr'] + i * 4)
                if c and c not in seen:
                    seen.add(c)
                    P(c); P(c + 4)
                    mlist(P(c + 8)); mlist(P(c + 12)); protos(P(c + 16)); plist(P(c + 20))
    for name in ('__objc_protolist', '__objc_selrefs', '__objc_classrefs', '__objc_superrefs', '__objc_protorefs'):
        s = m.sect(name)
        if s:
            for i in range(s['size'] // 4):
                v = P(s['addr'] + i * 4)
                if name == '__objc_protolist':
                    proto(v)
    s = m.sect('__cfstring')
    if s:
        for i in range(s['size'] // 16):
            P(s['addr'] + i * 16 + 8)
    for name in ('__mod_init_func', '__mod_term_func'):
        s = m.sect(name)
        if s:
            for i in range(s['size'] // 4):
                v = P(s['addr'] + i * 4)
                if v:
                    imps.add(v)
    return ptrs, imps


class CodeScan:
    """Descenso recursivo sobre __text (ARM y Thumb) con capstone."""

    def __init__(self, m, roots, known_funcs, extra=()):
        import capstone as cs
        self.m = m
        self.cs_arm = cs.Cs(cs.CS_ARCH_ARM, cs.CS_MODE_ARM)
        self.cs_thumb = cs.Cs(cs.CS_ARCH_ARM, cs.CS_MODE_THUMB)
        for c in (self.cs_arm, self.cs_thumb):
            c.detail = True
        self.cs = cs
        t = m.sect('__text', '__TEXT')
        self.t0, self.t1 = t['addr'], t['addr'] + t['size']
        self.tbytes = m.d[t['off']:t['off'] + t['size']]
        # además de __text: código que el Mach-O no marca como tal (Marmalade: la app Thumb en __symbolstub1)
        self.ranges = [(self.t0, self.t1)] + list(extra)
        self.code = set()  # direcciones de instrucción decodificadas
        self.tcode = set()  # ... de ellas, las decodificadas en modo Thumb
        self.code_hw = set()  # medias palabras cubiertas por instrucciones (para no rebasear pares Thumb)
        self.literals = {}  # addr literal -> (uso, dirección de la instrucción)
        self.funcs = set(known_funcs)
        self.jump_tables = set()
        self.ends = set()  # dirección siguiente a un salto incondicional (posible inicio de función)
        # stubs de asignadores y de GL/AL: lo que se les pasa es un tamaño o un enum (GL_VERTEX_ARRAY =
        # 0x8074 cae dentro de __text), nunca un puntero a código
        self.alloc_stubs = set()
        iso = m.dysym[12]
        for s in m.sects:
            if s['flags'] & 0xFF == S_STUBS and s['r2']:
                for j in range(s['size'] // s['r2']):
                    idx = struct.unpack_from('<I', m.d, iso + (s['r1'] + j) * 4)[0]
                    if not idx & (INDIRECT_LOCAL | INDIRECT_ABS) and (m.syms[idx][0] in (
                            '__Znwm', '__Znam', '__Znwj', '__Znaj', '_malloc', '_calloc', '_realloc')
                            or m.syms[idx][0].startswith(('_gl', '_al'))):
                        self.alloc_stubs.add(s['addr'] + j * s['r2'])
        # asignadores de Marmalade (funciones del propio binario, bl directo): el argumento es un tamaño que
        # puede coincidir con un inicio de función (PES 2012 s3eMemoryInit: malloc(0x11170) -> 0x98011170)
        for n, typ, sect, desc, val in m.syms:
            if not typ & N_STAB and (typ & N_TYPE) == N_SECT and n.startswith(('_s3eMalloc', '_s3eRealloc',
                                                                               '_s3eCalloc', '_s3eEdkMalloc', '_s3eEdkRealloc')):
                self.alloc_stubs.add(val)
        self.bl_targets = set()
        self.forced = set()  # raíces verificadas en consola (tools/code_roots.txt): nunca se frenan
        # ___switch8 de Apple (switch Thumb-1): tras el bl/blx va N y N+1 bytes; destino = lr + 2*byte
        self.switch8 = set()
        for lo, hi in self.ranges:
            for a in range(lo, hi - 4, 4):
                if self.m.u32(a) == 0xE55EC001 and self.m.u32(a + 4) == 0xE153000C:
                    self.switch8.add(a)
        self.inline_tables = set()  # palabras que solapan tablas de switch en línea (bytes, nunca punteros)
        self.todo = list(roots)

    def run(self):
        cs = self.cs
        from capstone import arm as A
        while self.todo:
            a = self.todo.pop()
            thumb = a & 1
            a &= ~1
            if not self.in_code(a) or a in self.code:
                continue
            md = self.cs_thumb if thumb else self.cs_arm
            pc = a
            last_cmp = {}
            first = True
            while self.in_code(pc) and pc not in self.code and pc not in self.literals:
                # no entrar a tablas de punteros desde una raíz/salto (la basura decodificada contamina raíces).
                # En flujo secuencial no: tras un push puede venir un par que forma "corrida" con el pool de
                # literales anterior (PES 2012 0x25072c, despachador GL), y los pools van tras saltos incondicionales
                # destino de bl/blx: solo frena una tabla con todos sus valores plausibles (PES 2012 0x2039a0, hoja
                # Thumb sin push: ldr/movs/movs/beq = 3 "punteros" -> nunca decodificada, rebaseada, bucle infinito)
                if first and pc not in self.forced and self.is_table(pc) and not (
                        pc in self.bl_targets and not self.table_all_plausible(pc)):
                    break
                first = False
                insn = next(md.disasm(self.fetch(pc), pc, 1), None)
                if insn is None:
                    break
                self.code.add(pc)
                if thumb:
                    self.tcode.add(pc)
                self.code_hw.update(range(pc, pc + insn.size, 2))
                if insn.cc in (A.ARM_CC_AL, A.ARM_CC_INVALID):
                    self.ends.add(pc + insn.size)
                mn = insn.insn_name()
                ops = insn.operands
                nxt = pc + insn.size
                cond = insn.cc not in (A.ARM_CC_AL, A.ARM_CC_INVALID) if hasattr(A, 'ARM_CC_AL') else False
                # cmp rX, #N (para tablas de salto)
                if mn == 'cmp' and len(ops) == 2 and ops[1].type == A.ARM_OP_IMM:
                    last_cmp[ops[0].reg] = ops[1].imm
                # literal: ldr rd, [pc, #imm]
                if mn in ('ldr', 'ldrb', 'ldrh', 'ldrsh', 'ldrsb', 'ldrd') and len(ops) >= 2 and ops[-1].type == A.ARM_OP_MEM \
                        and ops[-1].mem.base == A.ARM_REG_PC:
                    mem = ops[-1].mem
                    if mem.index == 0:
                        base = (pc + (4 if thumb else 8)) & ~3
                        disp = mem.disp if not getattr(ops[-1], 'subtracted', False) else -mem.disp
                        lit = base + disp
                        if mn == 'ldr':
                            use = self._classify_use(md, nxt, ops[0].reg, insn.cc) if ops[0].reg != A.ARM_REG_PC else 'ptr'
                            self.literals.setdefault(lit, (use, pc))
                            if ops[0].reg == A.ARM_REG_PC:
                                v = self.m.u32(lit)
                                if self.in_code(v & ~1):
                                    self.todo.append(v)
                                if not cond:
                                    break
                        else:
                            self.literals.setdefault(lit, ('int', pc))
                            if mn == 'ldrd':
                                self.literals.setdefault(lit + 4, ('int', pc))
                    elif ops[0].reg == A.ARM_REG_PC and mem.index and ops[-1].shift.value == 2 and not thumb:
                        # ldrls pc, [pc, rX, lsl #2] + tabla de direcciones absolutas. Fuera de __text solo si todas
                        # las entradas son case cercanos (Thumb mal decodificado como ARM inventaba tablas)
                        n = last_cmp.get(mem.index)
                        if n is not None and insn.cc == A.ARM_CC_LO:
                            n -= 1  # ldrcc/ldrlo: índices < n (ldrls: <= n)
                        if n is not None and not (self.t0 <= pc < self.t1) and not all(
                                abs(self.m.u32(pc + 8 + k * 4) - pc) < 0x4000 and not self.m.u32(pc + 8 + k * 4) & 3
                                for k in range(n + 1)):
                            n = None
                        if n is not None:
                            tbl = pc + 8
                            for k in range(n + 1):
                                ta = tbl + k * 4
                                self.jump_tables.add(ta)
                                self.literals[ta] = ('ptr', pc)
                                self.todo.append(self.m.u32(ta) | thumb)
                            nxt = tbl + (n + 1) * 4
                        if not cond:
                            break
                        pc = nxt
                        continue
                if mn in ('vldr',) and ops[-1].type == A.ARM_OP_MEM and ops[-1].mem.base == A.ARM_REG_PC:
                    base = (pc + (4 if thumb else 8)) & ~3
                    lit = base + ops[-1].mem.disp * (-1 if getattr(ops[-1], 'subtracted', False) else 1)
                    for k in range(0, 8 if ops[0].reg >= A.ARM_REG_D0 and ops[0].reg <= A.ARM_REG_D31 else 4, 4):
                        self.literals.setdefault(lit + k, ('int', pc))
                # ramas
                if thumb and mn in ('bl', 'blx') and ops and ops[0].type == A.ARM_OP_IMM and (ops[0].imm & ~1) in self.switch8:
                    n = self.m.d[self.m.file_off(nxt)]
                    for k in range(n + 1):
                        self.todo.append((nxt + 2 * self.m.d[self.m.file_off(nxt + 1 + k)]) | 1)
                    self.inline_tables.update(range((nxt) & ~3, (nxt + 2 + n + 3) & ~3, 4))
                    break  # lo que sigue al bl es la tabla, no código
                if mn in ('bl', 'blx') and ops and ops[0].type == A.ARM_OP_IMM:
                    tgt = ops[0].imm
                    mode = thumb ^ (1 if mn == 'blx' else 0)
                    self.funcs.add(tgt | mode)
                    self.bl_targets.add(tgt & ~1)
                    self.todo.append(tgt | mode)
                elif mn in ('b', 'cbz', 'cbnz') and ops and ops[-1].type == A.ARM_OP_IMM:
                    self.todo.append(ops[-1].imm | thumb)
                    if mn == 'b' and not cond and not (thumb and insn.cc != A.ARM_CC_AL and insn.size == 2):
                        if insn.cc in (A.ARM_CC_AL, A.ARM_CC_INVALID):
                            break
                elif mn == 'bx' or (mn in ('pop', 'ldm', 'ldmia', 'ldmfd') and any(
                        o.type == A.ARM_OP_REG and o.reg == A.ARM_REG_PC for o in ops)):
                    if insn.cc in (A.ARM_CC_AL, A.ARM_CC_INVALID):
                        break
                elif mn == 'add' and len(ops) == 3 and ops[0].reg == A.ARM_REG_PC and ops[1].type == A.ARM_OP_REG \
                        and ops[1].reg == A.ARM_REG_PC and ops[2].type == A.ARM_OP_REG and ops[2].shift.value == 2:
                    # addls pc, pc, rX, lsl #2 + tabla de `b` (relativa: no se rebasea, pero es código)
                    n = last_cmp.get(ops[2].reg)
                    if n is not None:
                        for k in range(n + 1):
                            self.todo.append((pc + 8 + k * 4) | thumb)
                    if insn.cc in (A.ARM_CC_AL, A.ARM_CC_INVALID):
                        break
                elif mn in ('mov', 'add', 'sub') and ops and ops[0].type == A.ARM_OP_REG and ops[0].reg == A.ARM_REG_PC:
                    if insn.cc in (A.ARM_CC_AL, A.ARM_CC_INVALID):
                        break
                elif mn == 'blx' and ops and ops[0].type == A.ARM_OP_REG:
                    pass
                elif mn == 'tbb' or mn == 'tbh':
                    break  # Thumb-2: tablas relativas, no hacen falta rebases
                pc = nxt

    def is_table(self, pc):
        # solo fuera de __text: palabra dentro de una corrida de >= 3 valores que apuntan a la imagen
        if self.t0 <= pc < self.t1:
            return False
        a = pc & ~3
        img = lambda x: 0x1000 <= self.m.u32(x) < self.m.hi and self.m.lo <= self.m.u32(x)
        # push {..., lr} Thumb + 2ª instrucción (b570 0004 = 0x0004b570) tras el pool de literales de la función
        # anterior parece la 3ª entrada de una tabla (PES 2012 0x25b710): un prólogo seguido de algo que no es un
        # puntero plausible no es entrada de tabla. Dentro de una vtable, 0x0025b5c4 sigue siendo entrada.
        # También push sin lr (0xB4, hoja): PES 2012 0xfda8c (fábrica Iw) = push {r4-r7}; movs r7,r1 = 0x000fb4f0.
        if not img(a) or ((self.m.u32(a) >> 8) & 0xFF in (0xB4, 0xB5) and not self.plausible(self.m.u32(a + 4))):
            return False
        # corrida de >= 3 valores-imagen alrededor de a
        lo = a
        while lo > a - 64 and img(lo - 4):
            lo -= 4
        hi = a
        while hi < a + 64 and img(hi + 4):
            hi += 4
        if (hi - lo) // 4 + 1 < 3:
            return False
        # ... y al menos un puntero plausible: varias instrucciones movs seguidas también forman "corridas"
        # (PES 2012 0x204ee8: 0x0005b538, 0x000c2000, 0x00299000 = push/movs/str)
        words = range(lo, hi + 4, 4)
        if any(self.plausible(self.m.u32(x)) for x in words):
            return True
        # tabla de switch: >= 3 entradas alineadas a los case de la misma función (PES 2012 0x26b200 -> 0x26b4f8)
        return sum(1 for x in words if not self.m.u32(x) & 3 and abs(self.m.u32(x) - x) < 0x4000) >= 3

    def table_all_plausible(self, a):
        # corrida de valores-imagen alrededor de a donde todos son punteros plausibles (tabla real). Sobre código
        # decodificado, 3 pares Thumb seguidos (PES 2012 0x2039a0: 0x00036b0a, 0x00110008, 0x0018d001) no lo son
        img = lambda x: 0x1000 <= self.m.u32(x) < self.m.hi and self.m.lo <= self.m.u32(x)
        lo = hi = a & ~3
        while lo > a - 64 and img(lo - 4):
            lo -= 4
        while hi < a + 64 and img(hi + 4):
            hi += 4
        return all(self.plausible(self.m.u32(x)) for x in range(lo, hi + 4, 4))

    def arm_vtable(self, a):
        # corrida de >= 3 valores alineados que apuntan a instrucciones ARM incondicionales de la app: vtable de
        # funciones ARM (PES 2012 0x2c9a30: 0x25b5c4 = ldr r0,[pc]; bx lr). Un par Thumb lo cumple muy rara vez.
        m = self.m
        ok = lambda x: (lambda v: not v & 3 and any(lo <= v < hi for lo, hi in self.ranges[1:])
                        and m.u32(v) >> 28 == 0xE)(m.u32(x))
        if not ok(a):
            return False
        n, x = 1, a - 4
        while ok(x) and n < 3:
            n, x = n + 1, x - 4
        x = a + 4
        while ok(x) and n < 3:
            n, x = n + 1, x + 4
        return n >= 3

    def plausible(self, v):
        m = self.m
        fo = m.file_off(v & ~1)
        if fo is None:  # zerofill (bss) de un segmento de datos
            return m.lo <= v < m.hi and not any(lo <= v < hi for lo, hi in self.ranges)
        if not any(g['vm'] <= v < g['vm'] + g['vs'] for g in m.segs if g['name'] == '__TEXT'):
            return True  # __DATA / __S3E_DATA
        sec = next((x for x in m.sects if x['seg'] == '__TEXT' and x['addr'] <= v < x['addr'] + x['size']), None)
        if sec and sec['name'] in ('__cstring', '__const', '__ustring', '__objc_methname', '__objc_classname'):
            return True
        if v & 1 and m.d[fo + 1] in (0xB4, 0xB5):
            return True  # entrada Thumb (un impar también puede ser cadena: sigue abajo)
        if v & 1:
            # función Thumb hoja (sin push): justo tras un retorno bx rX / pop {..pc}, con o sin relleno
            ret = lambda h: (h & 0xFF87) == 0x4700 or h >> 8 == 0xBD
            h1, h2 = struct.unpack_from('<HH', m.d, fo - 4)
            if ret(h2) or (h2 in (0, 0x46C0) and ret(h1)):
                return True
        if not v & 3 and (m.u32(v) & 0xFFFF4000) == 0xE92D4000:
            return True  # entrada ARM
        if not v & 3 and v >= 4:
            p = m.u32(v - 4)  # función ARM hoja aún no decodificada: justo tras bx lr / pop {..pc}
            if (p & 0xFFFFFFF0) == 0xE12FFF10 or (p & 0xFFFF8000) == 0xE8BD8000:
                return True
        fs = m.file_off(v)  # offset exacto (fo es de v & ~1)
        k = asc = 0  # dentro de una cadena C (sufijos fusionados: "AES128-SHA" en "PSK-AES128-SHA"; UTF-8: "#£$%")
        while k < 64 and (0x20 <= m.d[fs + k] < 0x7F or 0xC2 <= m.d[fs + k] <= 0xF4 or 0x80 <= m.d[fs + k] < 0xC0):
            asc += 0x20 <= m.d[fs + k] < 0x7F
            k += 1
        if k >= 3 and m.d[fs + k] == 0 and asc * 10 >= k * 7:
            return True
        if not v & 3 and v in self.code:
            # función ARM hoja (sin stmfd: ldr r0,[pc]; bx lr): ya decodificada y justo tras un fin de función
            p = m.u32(v - 4)
            if (p & 0xFFFFFFF0) == 0xE12FFF10 or (p & 0xFFFF8000) == 0xE8BD8000 or p >> 24 == 0xEA or (v - 4) not in self.code:
                return True
        if not v & 3 and v not in self.code_hw and m.lo <= m.u32(v) < m.hi and m.u32(v) >= 0x1000:
            return True  # puntero a una tabla de punteros (RTTI: typeinfo -> vtable de __class_type_info)
        return m.d[fs - 1] == 0 and all(0x20 <= c < 0x7F for c in m.d[fs:fs + 3])  # inicio de cadena

    def in_code(self, a):
        return any(lo <= a < hi for lo, hi in self.ranges)

    def fetch(self, pc, n=4):
        fo = self.m.file_off(pc)
        return self.m.d[fo:fo + n] if fo is not None else b''

    def _classify_use(self, md, start, reg, cc=None):
        """Mira las instrucciones siguientes: ¿el registro se usa como dirección o como número?"""
        from capstone import arm as A
        pc = start
        for _ in range(12):
            if not self.in_code(pc):
                break
            insn = next(md.disasm(self.fetch(pc), pc, 1), None)
            if insn is None:
                break
            mn = insn.insn_name()
            ops = insn.operands
            if cc not in (None, A.ARM_CC_AL) and insn.cc not in (cc, A.ARM_CC_AL, A.ARM_CC_INVALID):
                pc += insn.size  # rama condicional opuesta: no usa este valor
                continue
            if mn in ('mul', 'mla', 'mls', 'smull', 'umull', 'smlal', 'umlal') and any(
                    o.type == A.ARM_OP_REG and o.reg == reg for o in ops[1:]):
                return 'mul'
            reads = [o for o in ops[1:] if o.type == A.ARM_OP_REG and o.reg == reg]
            # salto incondicional (thunk ldr ip,=f; ldr pc,=g; bx; pop {pc}; mov pc): lo que sigue no es código de
            # este flujo. r0-r3/ip vivos son argumentos (PES 2012 0x30a8ec: ip = _s3eGlBindTexture salía 'int')
            if insn.cc in (A.ARM_CC_AL, A.ARM_CC_INVALID) and (
                    (mn.startswith('ldr') and ops and ops[0].type == A.ARM_OP_REG and ops[0].reg == A.ARM_REG_PC)
                    or (mn in ('bx', 'mov') and ops and ops[0].type == A.ARM_OP_REG and (mn == 'bx' or ops[0].reg == A.ARM_REG_PC))
                    or (mn in ('pop', 'ldm') and any(o.type == A.ARM_OP_REG and o.reg == A.ARM_REG_PC for o in ops))):
                if mn == 'bx' and ops[0].reg == reg:
                    return 'ptr'
                return 'arg' if reg in (A.ARM_REG_R0, A.ARM_REG_R1, A.ARM_REG_R2, A.ARM_REG_R3, A.ARM_REG_R12) else 'unknown'
            # PIC: add rX, pc, reg / ldr rX, [pc, reg] -> el literal es un desplazamiento, no puntero
            # (pop/ldm con pc en la lista no es PIC: es un retorno)
            # Thumb de dos operandos: add rX, pc (PES 2012 0x7f8b6: ldr r4,=0x281014; add r4, pc)
            if mn == 'add' and len(ops) == 2 and ops[0].type == A.ARM_OP_REG and ops[0].reg == reg \
                    and ops[1].type == A.ARM_OP_REG and ops[1].reg == A.ARM_REG_PC:
                return 'pcrel'
            if reads and not mn.startswith(('pop', 'ldm', 'push', 'stm')) and any(
                    o.type == A.ARM_OP_REG and o.reg == A.ARM_REG_PC for o in ops[1:]):
                return 'pcrel'
            if any(o.type == A.ARM_OP_MEM and o.mem.base == A.ARM_REG_PC and o.mem.index == reg for o in ops):
                return 'pcrel'
            # base o índice: ldr r3, [r4, r5] con r5 = tabla y r4 = desplazamiento (PES 2012 _s3eExtReadConfig)
            memuse = [o for o in ops if o.type == A.ARM_OP_MEM and reg in (o.mem.base, o.mem.index)]
            if memuse:
                return 'ptr'
            if mn in ('blx', 'bx') and ops and ops[0].type == A.ARM_OP_REG and ops[0].reg == reg:
                return 'ptr'
            if mn.startswith(('str', 'push', 'stm')) and any(o.type == A.ARM_OP_REG and o.reg == reg for o in ops):
                return 'store'
            if mn in ('cmp', 'cmn', 'tst', 'teq', 'mul', 'mla', 'mls', 'and', 'orr', 'eor', 'bic', 'lsl', 'lsr',
                      'asr', 'ror', 'rsb', 'sdiv', 'udiv', 'smull', 'umull', 'vmov', 'mvn', 'clz', 'uxth',
                      'uxtb', 'sxth', 'sxtb', 'ubfx', 'sbfx', 'smlal', 'umlal') and (
                    reads or (ops and ops[0].type == A.ARM_OP_REG and ops[0].reg == reg and mn in ('cmp', 'cmn', 'tst'))):
                return 'int'
            if mn in ('add', 'sub') and reads:
                return 'arith'
            if mn == 'mov' and reads:
                return 'move'
            if mn in ('bl', 'blx', 'b'):
                if ops and ops[0].type == A.ARM_OP_IMM and ops[0].imm in self.alloc_stubs:
                    return 'int'
                return 'arg' if reg <= A.ARM_REG_R3 or reg in (A.ARM_REG_R0, A.ARM_REG_R1, A.ARM_REG_R2, A.ARM_REG_R3) else 'unknown'
            if ops and ops[0].type == A.ARM_OP_REG and ops[0].reg == reg and not mn.startswith(('str', 'cmp', 'tst')):
                return 'dead'  # se sobrescribe sin leerse
            pc += insn.size
        return 'unknown'


def s3e_decrypt(path, m):
    """Marmalade: los datos de la app (__S3E_DATA, tras la cabecera XE3U + icf) van cifrados con una clave =
    hash del código (s3eGetCodeHash: h = h*31 + byte desde 1234 sobre [__text, fin de __symbolstub1), leído en
    direcciones absolutas). En la Vita ni dladdr ni esas direcciones existen, y parchear __TEXT cambia el hash: se
    descifra aquí el binario de staging (patch.c anula s3eDecodeData) y así los punteros de esos datos se rebasean.
    Devuelve (inicio, fin) de la región descifrada, o None."""
    sec = {s['name']: s for s in m.sects}
    s3e = next((s for s in m.sects if s['seg'] == '__S3E_DATA'), None)
    if not s3e or '__text' not in sec or '__symbolstub1' not in sec or m.d[s3e['off']:s3e['off'] + 4] != b'XE3U':
        return None
    h = struct.unpack_from('<18I', m.d, s3e['off'])
    start, n = h[11] + h[12] + h[15], h[6] - h[17]
    fo = s3e['off'] + start
    if not (0 < start and 0 < n and start + n <= s3e['size']):
        return None
    region = (s3e['addr'] + start, s3e['addr'] + start + n)
    if m.d[fo:fo + n].count(0) * 4 > n:  # ya descifrado (datos en claro: >25 % ceros; cifrado: ~0.4 %)
        return region
    t = m.text
    lo, hi = sec['__text']['addr'], sec['__symbolstub1']['addr'] + sec['__symbolstub1']['size']
    key = 1234
    for b in m.d[t['fo'] + lo - t['vm']:t['fo'] + hi - t['vm']]:
        key = (key * 31 + b) & 0xFFFFFFFF
    buf, k = bytearray(m.d[fo:fo + n]), key & 0xFFFF
    for i, b in enumerate(buf):  # s3eDecodeData (sin el desvío anti-debug por tiempo)
        buf[i] = b ^ (k >> 8)
        k = ((k + b) * 0xCE6D + 0x58BF) & 0xFFFF
    assert buf.count(0) * 4 > n, 's3e: la clave 0x%08x no descifra los datos' % key
    with open(path, 'r+b') as f:
        f.seek(m.slice_off + fo)
        f.write(buf)
    print('s3e: datos descifrados en el binario (clave 0x%08x, 0x%x bytes)' % (key, n))
    m.d = m.d[:fo] + bytes(buf) + m.d[fo + n:]
    return region


def main():
    args = sys.argv[1:]
    arch = None
    if '--arch' in args:
        i = args.index('--arch'); arch = args[i + 1]; del args[i:i + 2]
    if len(args) != 2:
        sys.exit(__doc__)
    path, outdir = args
    m = MachO(path, arch)
    s3e_plain = s3e_decrypt(path, m)
    rep = []
    if m.dyldinfo and m.pie:
        rebases, binds = dyld_info_tables(m)
        rebases = sorted(set(rebases))
        # binds (sobre todo weak) a símbolos que define el propio binario: van a esa definición
        defined = {}
        for name, typ, sect, desc, val in m.syms:
            if not typ & N_STAB and (typ & N_TYPE) == N_SECT and (typ & 1):
                defined.setdefault(name, val | (1 if desc & N_ARM_THUMB_DEF else 0))
        nlocal = 0
        for i, (a, add, lazy, name) in enumerate(binds):
            if name in defined:
                binds[i] = (a, defined[name] + add, lazy, '')
                nlocal += 1
        # varios binds al mismo slot (weak + regular): queda el último, como en dyld
        last = {}
        for b in binds:
            last[b[0]] = b
        binds = list(last.values())
        rep.append('modo: PIE/dyld_info (exacto); %d binds resueltos dentro de la imagen' % nlocal)
    else:
        binds, exact = classic_binds(m)
        bound = {b[0] for b in binds}
        optr, imps = objc_walk(m, bound)
        exact |= optr
        exact -= bound
        # cadenas (inicios)
        cstarts = set()
        for s in m.sects:
            if s['name'] in ('__cstring', '__objc_methname', '__objc_classname', '__objc_methtype', '__ustring'):
                a, end = s['addr'], s['addr'] + s['size']
                prev0 = True
                fo = s['off']
                for k in range(s['size']):
                    if prev0:
                        cstarts.add(a + k)
                    prev0 = m.d[fo + k] == 0
        datasecs = [s for s in m.sects if s['seg'] != '__TEXT']
        dlo = min(s['addr'] for s in datasecs)
        # hasta el final del segmento, no de su última sección: __S3E_DATA tiene vmsize 0x2c000 y su sección acaba
        # en 0x10111d0; la cola es la bss del .s3e (PES 2012: 1203 punteros a 0x0102xxxx sin rebase)
        dhi = max([s['addr'] + s['size'] for s in datasecs] +
                  [g['vm'] + g['vs'] for g in m.segs if g['name'] not in ('__PAGEZERO', '__TEXT', '__LINKEDIT')])
        # dlo..dhi cubre también el hueco sin mapear entre __DATA y __S3E_DATA: exigir un segmento real
        dsegs = [(g['vm'], g['vm'] + g['vs']) for g in m.segs if g['name'] not in ('__PAGEZERO', '__TEXT', '__LINKEDIT')]

        def in_data(v):
            return any(a <= v < b for a, b in dsegs)
        tconst = [s for s in m.sects if s['seg'] == '__TEXT' and s['name'] not in ('__text', '__cstring', '__symbol_stub4', '__ustring')]
        # raíces de código
        known = set()
        for n, typ, sect, desc, val in m.syms:
            if not typ & N_STAB and (typ & N_TYPE) == N_SECT and sect == 1:
                known.add(val | (1 if desc & N_ARM_THUMB_DEF else 0))
        roots = set(imps) | known
        # código confirmado en consola que la heurística de tablas no alcanza (pares Thumb que parecen punteros)
        forced = set()
        rf = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'code_roots.txt')
        if os.path.exists(rf):
            for l in open(rf):
                l = l.split('#')[0].strip()
                if l:
                    forced.add(int(l, 16))
        roots |= forced
        if m.entry:
            roots.add(m.entry)
        # punteros a código en datos (iteración: vtables, callbacks)
        stub1 = m.sect('__symbolstub1', '__TEXT') if s3e_plain else None
        s1 = (stub1['addr'], stub1['addr'] + stub1['size']) if stub1 and not stub1['flags'] & 0xFF else None
        scan = CodeScan(m, roots, known | set(imps), [s1] if s1 else [])
        scan.forced = {r & ~1 for r in forced}
        scan.run()
        if s1:
            # Marmalade: __symbolstub1 (flags 0) es la app en Thumb + sus tablas. Raíces: punteros Thumb en
            # __DATA y en los datos s3e descifrados; luego tablas de punteros de la propia sección (vtables)
            # que no caen sobre código. Lo que resulte código no pasa por la heurística de datos (los pares
            # de instrucciones Thumb parecen punteros: PES 2012 0xbc1a0 = bne + movs r1,r5 -> 0x0029d10b).
            def thumb_ptr(v):
                return v & 1 and s1[0] <= v < s1[1]

            def thumb_entry(v):
                # raíz heurística: un impar hacia la app puede ser un char* a la rodata (nombres RTTI) o ruido;
                # solo cuenta si empieza como función (push {...}). "Función conocida" no sirve: un bl decodificado
                # desde basura la contamina (PES 2012: 0x2d7427 -> tabla RTTI desensamblada como código)
                fo = m.file_off(v & ~1)
                return m.d[fo + 1] in (0xB4, 0xB5)
            for g in m.segs:
                if g['name'] in ('__DATA', '__S3E_DATA'):
                    for a in range(g['vm'], g['vm'] + g['fs'] - 3, 4):
                        if g['name'] == '__S3E_DATA' and not s3e_plain[0] <= a < s3e_plain[1]:
                            continue
                        if thumb_ptr(m.u32(a)) and thumb_entry(m.u32(a)):
                            scan.todo.append(m.u32(a))
            scan.run()
            def image_val(v):
                return m.lo <= v < m.hi
            for _ in range(8):
                # tablas de punteros Thumb de la sección (vtables) que no caen sobre código
                new, run = [], []
                for a in list(range(s1[0], s1[1] - 3, 4)) + [None]:
                    ok = a is not None and a not in scan.code_hw and a + 2 not in scan.code_hw and thumb_ptr(m.u32(a))
                    if ok and (not run or run[-1] == a - 4):
                        run.append(a)
                        continue
                    if len(run) >= 2:
                        new += [m.u32(x) for x in run if (m.u32(x) & ~1) not in scan.code and thumb_entry(m.u32(x))]
                    run = [a] if ok else []
                # prólogos en huecos (Thumb push {..., lr}; ARM stmfd sp!, {..., lr}: la app es sobre todo ARM),
                # salvo dentro de una tabla. No basta "la palabra parece puntero": push {r4-r6,lr} + movs r4,r0 =
                # 0x0004b570 cae en la imagen (PES 2012 0x25b710 quedaba sin descubrir y con pares rebaseados)
                for a in range(s1[0], s1[1] - 1, 2):
                    if a in scan.code_hw or scan.is_table(a) or scan.arm_vtable(a & ~3):
                        continue
                    if m.d[m.file_off(a) + 1] == 0xB5:
                        new.append(a | 1)
                    elif not a & 3 and (m.u32(a) & 0xFFFF4000) == 0xE92D4000:
                        new.append(a)
                new = [v for v in new if (v & ~1) not in scan.code]
                if not new:
                    break
                scan.todo.extend(new)
                scan.run()

        def in_text(v):
            return scan.t0 <= (v & ~1) < scan.t1

        def looks_func(v):
            if v in scan.funcs or (v & ~1) in scan.funcs or v in imps:
                return True
            return False

        def prologue(v):
            if v & 1 or v & 3 or not in_text(v):
                return False
            w = m.u32(v)
            return (w & 0xFFFF4000) == 0xE92D4000 or w == 0xE1A0C00D or (w & 0xFFFFF000) == 0xE52DE000

        def s1_strong(v):
            # sobre código desensamblado solo se acepta un valor que apunte a algo que un puntero real señala:
            # datos, cadenas/constantes de __TEXT, inicio de función conocida o tablas (no-código) de la app
            if in_data(v) or v in cstarts:
                return True
            # __TEXT,__const solo alineado: un par Thumb decodificado cae ahí en dirección impar (PES 2012 0x2b59fc =
            # bpl + movs r7,r0 -> 0x0007d539, ceros de __const: r7 basura, objeto "sin vtable")
            if not v & 3 and any(t['addr'] <= v < t['addr'] + t['size'] for t in tconst if t is not stub1):
                return True
            if v in scan.funcs or (v | 1) in scan.funcs or (v & ~1) in scan.funcs:
                return True
            if not v & 3 and m.lo <= v < m.hi and (m.u32(v) & 0xFFFF4000) == 0xE92D4000:
                return True  # apunta justo a un prólogo ARM: entrada de vtable
            # dentro de la app, fuera del código: solo inicio de cadena (nombres RTTI: typeinfo ->
            # "10TSingletonI24CPlayer..."). Un destino "alineado" no basta: 1 de cada 4 pares Thumb lo es
            # (PES 2012 0xbc1c0 = blx + movs r0,r4 -> 0x0020e8ac: la función devolvía basura)
            if s1 is None or not s1[0] <= v < s1[1] or (v & ~1) in scan.code_hw:
                return False
            fo = m.file_off(v)
            return m.d[fo - 1] == 0 and all(0x20 <= c < 0x7F for c in m.d[fo:fo + 3])

        def prologue_at(v):
            return not v & 3 and m.lo <= v < m.hi and m.file_off(v) is not None and (m.u32(v) & 0xFFFF4000) == 0xE92D4000

        def s1_code_ptr(a):
            # s1_strong + contexto para punteros a datos sobre código decodificado: en corrida, o alineado con un
            # vecino de dato (chico, valor-imagen, texto). Aislado entre instrucciones es un par Thumb (PES 2012
            # 0x18bcc8 = ldr r1,[r5,#8]; lsls r0,r4,#2 -> 0x00a068a9: r0 basura)
            v = m.u32(a)
            if not s1_strong(v):
                return False
            fo = m.file_off(a)
            # push {..,lr} Thumb decodificado: entrada de función (PES 2012 0x1f3148 = 0x0004b57c tras un literal).
            # No en thunks Thumb->ARM (bx pc; ldr ip,[pc]; ldr pc,[pc]; .word fn): el literal fn puede ser 0x0003b5xx
            if a in scan.tcode and m.d[fo + 1] == 0xB5 and (m.u32(a - 4) & 0xFFFFF000) != 0xE59FF000:
                return False
            nb = ((m.u32(a - 4), m.d[fo - 4:fo]), (m.u32(a + 4), m.d[fo + 4:fo + 8]))
            text = lambda b: all(0x20 <= c < 0x7F for c in b)
            if in_data(v):
                if in_data(nb[0][0]) or in_data(nb[1][0]):
                    return True
                return not v & 3 and any(w < 0x10000 or m.lo <= w < m.hi or text(b) for w, b in nb)
            if not v & 1 and s1[0] <= v < s1[1] and (v | 1) in scan.funcs and v not in scan.funcs:
                return False  # valor par hacia una función Thumb: no es un puntero a código válido
            # función / prólogo ARM / cadena: entre instrucciones suele ser un par Thumb (PES 2012 0x133540 =
            # movs r0,r4; movs r1,r5 -> 0x00290020 = push {r4,lr}: setName(NULL); 0x2039a8 = beq; movs r0,r3 ->
            # 0x0018d001 = función Thumb). En vtables/tablas el vecino es dato o función.
            return any(w < 0x10000 or text(b) or in_data(w) or w in cstarts or prologue_at(w) or w in scan.funcs
                       for w, b in nb)

        def s1_vtable(a):
            # >= 2 punteros Thumb seguidos hacia la app = vtable aunque el desensamblado haya entrado en ella
            # destino = función conocida o push {.., lr}: dos pares Thumb `movs` seguidos también son impares hacia
            # stub1 (PES 2012 0xbc8e8 = movs r3,r0; movs r2,r4 -> 0x00220003: read() con tamaño basura)
            tp = lambda x: x & 1 and s1[0] <= x < s1[1] and (x in scan.funcs or m.d[m.file_off(x & ~1) + 1] == 0xB5)
            return tp(m.u32(a)) and (tp(m.u32(a - 4)) or tp(m.u32(a + 4)))

        # recursos incrustados en los datos s3e (PES 2012: BMP de 32x32 en 0x100e058 abierto con
        # s3eFileOpenFromMemory; rebasear su cabecera DIB pedía 758 MB al decodificarlo)
        blobs = []
        if s3e_plain:
            fo0 = m.file_off(s3e_plain[0])
            raw = m.d[fo0:fo0 + s3e_plain[1] - s3e_plain[0]]
            i = raw.find(b'BM')
            while i >= 0:
                if i + 18 <= len(raw):
                    size, off, dib = struct.unpack_from('<I', raw, i + 2)[0], struct.unpack_from('<I', raw, i + 10)[0], \
                        struct.unpack_from('<I', raw, i + 14)[0]
                    if dib in (12, 40, 56, 108, 124) and 14 + dib <= off < size <= len(raw) - i:
                        blobs.append((s3e_plain[0] + i, s3e_plain[0] + i + size))
                i = raw.find(b'BM', i + 1)
            i = raw.find(b'\x89PNG\r\n\x1a\n')
            while i >= 0:
                e = raw.find(b'IEND', i)
                if e > 0:
                    blobs.append((s3e_plain[0] + i, s3e_plain[0] + e + 8))
                i = raw.find(b'\x89PNG\r\n\x1a\n', i + 1)
            if blobs:
                rep.append('recursos incrustados (no se rebasean): ' + ', '.join('0x%x-0x%x' % b for b in blobs))

        # constantes de SQLite (tablas de bytes del tokenizador y del parser): sqlite3UpperToLower ... fin de las
        # tablas de keywords tras zText. Sus palabras parecen punteros (0x00060400, 0x01006700) y rebaseadas el
        # tokenizador no ve el SQL (prepare OK sin sentencia -> step(NULL) = 21; PES 2012 PRAGMA integrity_check)
        sqlite_rng = None
        i = m.d.find(bytes(range(64)) + b'@abcdefghijklmnopqrstuvwxyz')
        k = m.d.find(b'REINDEXEDESCAPEACHECK')
        if i >= 0 and k > i and k - i < 0x10000:
            sqlite_rng = (i + 0x1000 - 0x100, k + 0x1000 + 0x700)  # __TEXT: vm = offset + 0x1000
            rep.append('constantes SQLite: 0x%x-0x%x' % sqlite_rng)

        def sqlite_const(a):
            # True = no rebasear. En la zona de constantes de SQLite solo hay punteros hacia la app (>= 0x200000):
            # tablas de métodos y de nombres de opcodes (en corrida) o una cadena suelta. Todo lo demás son tablas
            # de bytes/u16 del tokenizador y del parser (PES 2012: 0x01006700, 0x00060400, 0x001d01b0 rebaseados
            # -> INSERT OR REPLACE se preparaba "OK" sin sentencia, transacción abierta, crash en el menú)
            if not sqlite_rng or not sqlite_rng[0] <= a < sqlite_rng[1]:
                return False
            v = m.u32(a)
            if not 0x200000 <= v < stub1['addr'] + stub1['size']:
                return True  # los punteros de esta zona van a código/cadenas de la app
            small = lambda x: all(b < 0x80 for b in struct.pack('<I', m.u32(x)))
            near = any(0x200000 <= m.u32(a + d) < stub1['addr'] + stub1['size'] for d in (-4, 4))
            # bytes chicos y no plausible: texto o tabla de bytes (hash de keywords 0x2c8d90 = 0x00275800, 0x0023006e),
            # salvo en una tabla de punteros (vecino no-chico hacia la app: 0x0025787c entre 0x00289344/0x00257884)
            if small(a) and not scan.plausible(v) and (not near or (small(a - 4) and small(a + 4))):
                return True  # bytes chicos (texto / tablas de bytes) que no apuntan a nada plausible
            # tabla u16 del parser: la mitad baja se repite en un vecino (0x003001b0, 0x003101b0, 0x002001b0)
            return v >> 16 < 0x800 and any((m.u32(a + d) & 0xFFFF) == (v & 0xFFFF) and m.u32(a + d) >> 16 < 0x800
                                           for d in (-4, 4))

        def u16_table(a):
            # >= 60 % de las palabras vecinas son pares de enteros chicos (o cero): tabla de u16, no de punteros
            ws = [m.u32(x) for x in range(a - 32, a + 36, 4)]
            # (un valor dentro de __DATA/__S3E_DATA no cuenta: tiras de punteros a globales 0x010107xx)
            small = sum(1 for w in ws if w == 0 or ((w >> 16) < 0x800 and (w & 0xFFFF) < 0x800 and not in_data(w)))
            return small * 10 >= len(ws) * 6

        data_words = []
        for s in m.sects:
            t = s['flags'] & 0xFF
            if t in (S_ZEROFILL, S_GB_ZEROFILL) or s['name'] in ('__text', '__cstring', '__ustring', '__symbol_stub4',
                                                                   '__objc_methname', '__objc_classname', '__objc_methtype'):
                continue
            if t in (S_LAZY, S_NON_LAZY):
                continue
            # Marmalade: el .s3e embebido (cabecera XE3U + icf + datos comprimidos) no lleva punteros del
            # Mach-O; sus tamaños caen en el rango de __TEXT (PES 2012: malloc(0x98014470) al cargarlo)
            if s['seg'] == '__S3E_DATA' and not s3e_plain:
                continue
            for k in range(0, s['size'] - 3, 4):
                a = s['addr'] + k
                if s['seg'] == '__S3E_DATA' and not s3e_plain[0] <= a < s3e_plain[1]:
                    continue
                if any(lo <= a < hi for lo, hi in blobs):
                    continue  # imagen incrustada: bytes, no punteros
                if a in scan.inline_tables:
                    continue  # tabla de ___switch8
                if sqlite_const(a):
                    continue
                if s['seg'] == '__TEXT' and (m.u32(a) >> 16) < 0x800 and (m.u32(a) & 0xFFFF) < 0x800 \
                        and u16_table(a) and not (scan.plausible(m.u32(a)) and not in_data(m.u32(a))):
                    continue  # tabla de u16 (parser de SQLite: 0x001d01b0 = 0x01b0, 0x001d)
                if s['seg'] == '__TEXT':
                    fo = m.file_off(a)
                    b = m.d[fo:fo + 4]
                    # texto dentro de una cadena (PES 2012 "ram://": "://\0" = 0x002f2f3a): el byte anterior también
                    # es texto. Un puntero de tabla va tras otra entrada (byte alto 0x00): 0x002d6f30 = "0o-\0" no
                    txt = lambda w: all(0x20 <= c < 0x7F or c == 0 for c in w) and sum(0x20 <= c < 0x7F for c in w) >= 2
                    # ... y la palabra anterior también es texto (tras un float 0x3fc90fdc el '?' engañaba: 0x218ed0)
                    if txt(b) and 0x20 <= m.d[fo - 1] < 0x7F and txt(m.d[fo - 4:fo]):
                        continue
                if a in bound or a in exact:
                    continue
                if s1 and s1[0] <= a < s1[1] and (a in scan.code_hw or a + 2 in scan.code_hw) and not s1_code_ptr(a) \
                        and not s1_vtable(a) and not scan.arm_vtable(a) \
                        and not (scan.is_table(a) and scan.plausible(m.u32(a)) and scan.table_all_plausible(a)
                                 and not (a - 4 in scan.code_hw and m.d[m.file_off(a - 4) + 1] == 0xB5)):
                    continue  # instrucción (par Thumb / ARM), no dato
                if s1 and s1[0] <= a < s1[1] and a - 4 in scan.code_hw and a in scan.code_hw \
                        and m.d[m.file_off(a - 4) + 1] == 0xB5:
                    continue  # justo tras un push Thumb decodificado: código seguro (0x81aac = ldr/movs)
                if s1 and s1[0] <= a < s1[1] and a in scan.literals:
                    continue  # literal de la app: lo decide la ruta de literales (PIC 'pcrel' no se rebasea)
                data_words.append(a)

        # pasada 1: punteros a código con prólogo -> nuevas raíces
        for _ in range(3):
            new = []
            for a in data_words:
                v = m.u32(a)
                if in_text(v) and not looks_func(v) and prologue(v):
                    new.append(v)
            if not new:
                break
            scan.funcs.update(new)
            scan.todo.extend(new)
            scan.run()

        for _ in range(4):
            new, run = [], []
            for a in data_words + [None]:
                v = m.u32(a) if a is not None else 0
                ok = a is not None and in_text(v) and (v & 3) == 0
                if ok and (not run or run[-1][0] == a - 4):
                    run.append((a, v)); continue
                if len(run) >= 2 and any(looks_func(x) or prologue(x) for _, x in run):
                    new += [x for _, x in run if (x & ~1) not in scan.code]
                run = [(a, v)] if ok else []
            if not new:
                break
            scan.funcs.update(new)
            scan.todo.extend(new)
            scan.run()

        # barrido de huecos: prólogos ARM (stmfd sp!, {..., lr}) fuera del código ya visto
        for _ in range(6):
            new = []
            for a in range(scan.t0, scan.t1, 4):
                if a in scan.code or a in scan.literals:
                    continue
                if prologue(a) or (a in scan.ends and m.u32(a) >> 28 == 0xE and m.u32(a + 4) >> 28 == 0xE):
                    new.append(a)
            if not new:
                break
            scan.funcs.update(new)
            scan.todo.extend(new)
            scan.run()

        # Inicios de función "fuertes": símbolos, IMPs de ObjC, destinos de bl, prólogo ARM. Los
        # "débiles" (instrucción tras un salto incondicional) solo cuentan con más evidencia.
        strong = {v & ~1 for v in known} | {v & ~1 for v in imps} | scan.bl_targets

        def is_strong(v):
            return (v & ~1) in strong or prologue(v & ~1)

        def is_weak(v):
            return (v & ~1) in scan.ends and (v & ~1) in scan.code and (v & 0xFF) != 0 and (v & 0xFFF0) != 0

        def in_ptr_sect(v):
            return in_data(v) or v in cstarts or any(s['addr'] <= v < s['addr'] + s['size'] for s in tconst)

        heur = set()
        doubt = []
        # vtables: corrida >=2 de punteros a código (todos fuertes o débiles, al menos uno fuerte),
        # precedida por la cabecera de vtable (0 / puntero a typeinfo) o compuesta solo de fuertes.
        vt = set()
        run = []
        for a in data_words + [None]:
            v = m.u32(a) if a is not None else 0
            ok = a is not None and in_text(v) and (v & 3) == 0 and (is_strong(v) or is_weak(v))
            if ok and (not run or run[-1][0] == a - 4):
                run.append((a, v))
                continue
            if len(run) >= 2:
                head = m.u32(run[0][0] - 4)
                header = head == 0 or in_ptr_sect(head)
                n_strong = sum(1 for _, x in run if is_strong(x))
                if n_strong == len(run) or (header and n_strong):
                    vt.update(x for x, _ in run)
            run = [(a, v)] if ok else []
        zfill = [s for s in m.sects if s['flags'] & 0xFF in (S_ZEROFILL, S_GB_ZEROFILL)]
        for a in data_words:
            v = m.u32(a)
            if v == 0 or not (m.lo <= v < m.hi):
                continue
            # tabla de solo lectura -> bss/common: son dos u16 (HAWX: lbase de zlib = 0x00a30083, ADPCM,
            # charsets de FreeType); rebasearlos rompe inflate y las fuentes
            if (any(s['addr'] <= a < s['addr'] + s['size'] for s in tconst)
                    and any(s['addr'] <= v < s['addr'] + s['size'] for s in zfill)):
                doubt.append(('tconst->zerofill', a, v))
                continue
            if in_ptr_sect(v):
                heur.add(a)
            elif in_text(v):
                if is_strong(v) or a in vt:
                    heur.add(a)
                else:
                    doubt.append(('data->text-nofunc', a, v))
            else:
                doubt.append(('data->other', a, v))
        # literales del código
        def func_entry(v):
            # exactamente el inicio de una función: Thumb (impar) con push {..., lr}, ARM con stmfd sp!, {..., lr}
            if not m.lo <= (v & ~1) < m.hi or m.file_off(v & ~1) is None:
                return False
            if v & 1:
                return m.d[m.file_off(v & ~1) + 1] == 0xB5
            return not v & 3 and (m.u32(v) & 0xFFFF4000) == 0xE92D4000

        lit_ptr = set()
        for lit, (use, ipc) in scan.literals.items():
            if lit in scan.jump_tables:
                lit_ptr.add(lit)
                continue
            v = m.u32(lit)
            if not (m.lo <= v < m.hi) or v < 0x1000:
                continue
            if use == 'pcrel':
                continue
            if use == 'int' and func_entry(v):
                lit_ptr.add(lit)  # cmp contra un puntero a función (PES 2012 0x19ff9c: cmp r1, =0xf6ab1)
                continue
            if use in ('int', 'mul') and in_text(v):
                doubt.append(('lit-int-use', lit, v))
                continue
            if in_ptr_sect(v):
                # fuera de __text un entero de >1 MB es raro: puntero salvo uso multiplicativo
                # o valor impar que no es inicio de cadena. En __DATA el impar con uso 'int' es un cmp contra
                # el buffer interno de un string SSO (HAWX: &str + 13; sin rebase -> delete de memoria estática)
                if use == 'mul' or (use == 'int' and v % 4 and v not in cstarts and not in_data(v)):
                    doubt.append(('lit-int-use', lit, v))
                else:
                    lit_ptr.add(lit)
            elif in_text(v):
                # < 0x10000 colisiona con enums GL (GL_TEXTURE0 = 0x84c0) y constantes (20000 ms): solo si es
                # símbolo/IMP/destino de bl, no basta un prólogo casual (HAWX: CCommonGLDriver::setTexture)
                if v < 0x10000 and (v & ~1) not in strong:
                    doubt.append(('lit->text-low', lit, v))
                # función con evidencia fuerte, o débil usada como callback/puntero (no como número)
                # desde la app (__symbolstub1) solo inicios de función reales: is_weak acepta casi cualquier
                # dirección de código (PES 2012: 86400 = 0x15180 como divisor, 77608 = 0x12f28 como tamaño)
                elif is_strong(v) or (is_weak(v) and use in ('arg', 'store', 'move', 'ptr') and not (s1 and s1[0] <= lit < s1[1])):
                    lit_ptr.add(lit)
                else:
                    doubt.append(('lit->text-nofunc', lit, v))
            elif use not in ('int', 'mul') and any(s['name'] == '__cstring' and s['addr'] <= v < s['addr'] + s['size']
                                                    for s in m.sects):
                # interior de una cadena: hash inlineado que arranca con los primeros caracteres como
                # constante (PES 2012 _s3eConfigReadFile: mov r0,#'S'; ldrb [lr,#-1] desde "S3E"+2)
                lit_ptr.add(lit)
            else:
                doubt.append(('lit->other', lit, v))
        # constantes de manipulación de bits que caen en la imagen (PES 2012: 0x01010101 en __S3E_DATA, usada por
        # el strcpy/strlen palabra a palabra de la app; rebaseada cortaba las rutas a 4 caracteres: "soun")
        magic = {0x01010101}
        heur = {a for a in heur if m.u32(a) not in magic}
        lit_ptr = {a for a in lit_ptr if m.u32(a) not in magic}
        rebases = sorted(exact | heur | lit_ptr)
        rep.append('modo: clásico sin relocs (heurístico + estructural)')
        rep.append('exactos (objc/cfstring/nl locales/stubs/relocs): %d' % len(exact))
        rep.append('datos heurísticos: %d' % len(heur))
        rep.append('literales de código: %d (de %d literales vistos, %d instrucciones)' % (
            len(lit_ptr), len(scan.literals), len(scan.code)))
        rep.append('tablas de salto: %d palabras' % len(scan.jump_tables))
        tsz = scan.t1 - scan.t0
        ntext = sum(1 for a in scan.code if scan.t0 <= a < scan.t1)
        rep.append('cobertura de __text: %.1f%% (%d de %d palabras)' % (100.0 * ntext * 4 / tsz, ntext, tsz // 4))
        if s1:
            nhw = sum(1 for a in scan.code_hw if s1[0] <= a < s1[1])
            rep.append('cobertura de __symbolstub1 (código Thumb/ARM): %.1f%% (%d medias palabras)' % (
                100.0 * nhw * 2 / (s1[1] - s1[0]), nhw))
        rep.append('dudosos (NO rebaseados): %d' % len(doubt))
        rep.append('funciones conocidas: %d' % len(scan.funcs))
        rep.append('')
        for kind, a, v in sorted(doubt, key=lambda x: x[1]):
            rep.append('DUDA %-22s 0x%08X = 0x%08X' % (kind, a, v))

    # palabras confirmadas en consola que la heurística no rebasea (tools/rebase_force.txt)
    rf = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rebase_force.txt')
    if os.path.exists(rf):
        extra = {int(l.split('#')[0], 16) for l in open(rf) if l.split('#')[0].strip()}
        rebases = sorted(set(rebases) | extra)
    with open(outdir + '/rebase.bin', 'wb') as f:
        f.write(struct.pack('<2I', 0x31534252, len(rebases)))
        f.write(struct.pack('<%dI' % len(rebases), *rebases))
    with open(outdir + '/binds.bin', 'wb') as f:
        f.write(struct.pack('<2I', 0x31444E42, len(binds)))
        for a, add, lazy, name in binds:
            f.write(struct.pack('<IIB', a, add & 0xFFFFFFFF, 1 if lazy else 0) + name.encode('latin1') + b'\0')
    names = defaultdict(int)
    for a, add, lazy, name in binds:
        if name:
            names[name] += 1
    hdr = ['ejecutable: %s  pie=%s  entry=0x%x  imagen=[0x%x,0x%x)' % (path, m.pie, m.entry or 0, m.lo, m.hi),
           'rebase: %d palabras   binds: %d slots / %d símbolos' % (len(rebases), len(binds), len(names))]
    with open(outdir + '/report.txt', 'w') as f:
        f.write('\n'.join(hdr + rep) + '\n\n# imports\n')
        for n in sorted(names):
            f.write('%s%s\n' % (n, '  [dato]' if is_data_name(n) else ''))
    print('\n'.join(hdr + rep[:12]))


if __name__ == '__main__':
    main()
