"""Build EVA_Setup: one .exe containing the complete update. Reuses the proven runtime of EVA.exe (bootloader, Python,
standard-library extensions), drops EVA's own heavy libraries, swaps the start-up script, and embeds the update zip."""
import hashlib, marshal, sys, os, zlib
sys.path.insert(0, '/home/claude/tools')
import exe_archive as A

VERSION = sys.argv[1]; UPDATE_ZIP = sys.argv[2]; OUT = sys.argv[3]
SRC_EXE = '/home/claude/eva_v090_inner/EVA.exe'
payload = open(UPDATE_ZIP, 'rb').read(); sha = hashlib.sha256(payload).hexdigest()
name = f'EVA_v{VERSION}_UPDATE.zip'
script = open('/home/claude/tools/setup_main.py', encoding='utf-8').read().replace('__VERSION__', VERSION).replace('__SHA256__', sha)
code = compile(script, 'setup_main.py', 'exec', dont_inherit=True)

x = A.read_exe(SRC_EXE)
KEEP_SCRIPTS = {'pyiboot01_bootstrap'}
entries, placed = [], False
for e in x['entries']:
    if e.typ == 's':
        if e.name in KEEP_SCRIPTS:
            entries.append(e)
        elif e.name == 'desktop' and not placed:                      # EVA's start-up script -> the installer's
            entries.append(A.Entry('setup_main', 's', zlib.compress(marshal.dumps(code), 9), len(marshal.dumps(code)), 1)); placed = True
        continue                                                       # other run-time hooks are for EVA's libraries
    if e.typ == 'b' and '\\' in e.name:                                # files inside EVA's own libraries (playwright, av, google, ...)
        continue
    if e.typ == 'o' and not any(en.name == name for en in entries):    # the update goes in just before the final entries
        entries.append(A.Entry(name, 'b', payload, len(payload), 0))
    entries.append(e)
assert placed and any(en.name == name for en in entries)
exe = A.build_exe(x['bootloader'], entries, x['pyver'], x['pylib'])
os.makedirs(OUT, exist_ok=True)
path = os.path.join(OUT, f'EVA_Setup_{VERSION}.exe')
open(path, 'wb').write(exe)
print(path, len(exe), 'bytes;', len(entries), 'entries (EVA.exe has', len(x['entries']), ')')
print('update sha256 embedded:', sha)
