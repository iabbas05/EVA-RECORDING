"""Read and rebuild the PyInstaller (6.x) one-file archive that sits at the end of EVA.exe.

Only the archive appended after the Windows bootloader is touched; the bootloader (the .exe program itself),
its icon, manifest and every unchanged entry are copied byte-for-byte.
"""
import marshal
import struct
import zlib

MAGIC = b'MEI\x0c\x0b\x0a\x0b\x0e'
COOKIE = '!8sIIII64s'
COOKIE_LEN = struct.calcsize(COOKIE)
TOC = '!IIIIBc'
TOC_LEN = struct.calcsize(TOC)


class Entry:
    def __init__(self, name, typ, stored, ulen, compressed):
        self.name, self.typ, self.stored, self.ulen, self.compressed = name, typ, stored, ulen, compressed

    def data(self):
        return zlib.decompress(self.stored) if self.compressed else self.stored

    def set_data(self, raw, compress=None):
        compress = self.compressed if compress is None else compress
        self.stored = zlib.compress(raw, 9) if compress else raw
        self.ulen, self.compressed = len(raw), int(compress)


def read_exe(path):
    blob = open(path, 'rb').read()
    cookie_at = blob.rfind(MAGIC)
    if cookie_at < 0:
        raise ValueError('not a PyInstaller archive')
    _, pkg_len, toc_off, toc_len, pyver, pylib = struct.unpack(COOKIE, blob[cookie_at:cookie_at + COOKIE_LEN])
    start = cookie_at + COOKIE_LEN - pkg_len
    entries, pos = [], start + toc_off
    while pos < start + toc_off + toc_len:
        elen, off, clen, ulen, flag, typ = struct.unpack(TOC, blob[pos:pos + TOC_LEN])
        name = blob[pos + TOC_LEN:pos + elen].rstrip(b'\0').decode('utf-8')
        entries.append(Entry(name, typ.decode('ascii'), blob[start + off:start + off + clen], ulen, flag))
        pos += elen
    return dict(bootloader=blob[:start], entries=entries, pyver=pyver, pylib=pylib.rstrip(b'\0'), blob=blob)


def build_exe(bootloader, entries, pyver, pylib):
    out, toc = bytearray(), []
    for e in entries:
        toc.append((len(out), len(e.stored), e.ulen, int(e.compressed), e.typ, e.name))
        out += e.stored
    toc_off, serial = len(out), bytearray()
    for off, clen, ulen, flag, typ, name in toc:
        nb = name.encode('utf-8'); nlen = len(nb) + 1
        if (TOC_LEN + nlen) % 16:
            nlen += 16 - (TOC_LEN + nlen) % 16
        serial += struct.pack(TOC + f'{nlen}s', TOC_LEN + nlen, off, clen, ulen, flag, typ.encode('ascii'), nb)
    out += serial
    pkg_len = toc_off + len(serial) + COOKIE_LEN
    out += struct.pack(COOKIE, MAGIC, pkg_len, toc_off, len(serial), pyver, pylib)
    return bytes(bootloader) + bytes(out)


# ---- PYZ (the archive of compiled modules) -------------------------------------------------------
def read_pyz(raw):
    assert raw[:4] == b'PYZ\0', 'bad PYZ'
    magic, toc_pos = raw[4:8], struct.unpack('!i', raw[8:12])[0]
    toc = marshal.loads(raw[toc_pos:])
    toc = dict(toc) if not isinstance(toc, dict) else toc
    mods = {n: (t, raw[p:p + l]) for n, (t, p, l) in toc.items()}
    return magic, raw[:17], mods


def build_pyz(magic, mods, order=None):
    names = order or list(mods)
    body, toc = bytearray(), []
    start = 17
    for n in names:
        t, blob = mods[n]
        toc.append((n, (t, start + len(body), len(blob))))
        body += blob
    toc_pos = start + len(body)
    return b'PYZ\0' + magic + struct.pack('!i', toc_pos) + b"\0\0\0\0\0" + bytes(body) + marshal.dumps(toc)


def module_blob(source, filename):
    """Compile to the bytecode PyInstaller stores: zlib(marshal(code))."""
    code = compile(source, filename, 'exec', dont_inherit=True, optimize=0)
    return zlib.compress(marshal.dumps(code), 9)
