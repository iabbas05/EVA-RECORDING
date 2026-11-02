"""Build EVA 0.9.7 from the 0.9.0 EVA.exe: swap only the changed modules and bundled files, keep everything else."""
import hashlib, io, json, os, sys, zipfile, time, zlib, marshal
sys.path.insert(0, '/home/claude/tools')
import exe_archive as A

OLD_EXE = '/home/claude/eva_v090_inner/EVA.exe'
SRC = '/home/claude/eva_v091'
OUT = '/home/claude/out097'
CHANGED = ['eva_portal.py', 'google_delivery.py', 'service.py', 'manager.py', 'diagnostics.py', 'index.html',
           'version.txt', 'README.md', 'TEST_CHECKLIST.md', 'recorder_repair.py', 'updater.py', 'delivery_queue.py', 'storage.py']
os.makedirs(OUT, exist_ok=True)

x = A.read_exe(OLD_EXE)
by_name = {e.name: e for e in x['entries']}
pyz_entry = next(e for e in x['entries'] if e.typ == 'z')
magic, hdr, mods = A.read_pyz(pyz_entry.data())
order = [n for n, _ in marshal.loads(pyz_entry.data()[__import__('struct').unpack('!i', pyz_entry.data()[8:12])[0]:])]

for name in CHANGED:
    raw = open(os.path.join(SRC, name), 'rb').read()
    assert name in by_name, name                      # only existing bundled files are replaced (no new names)
    by_name[name].set_data(raw, compress=True)        # bundled copy used for the installer's checksum comparison
    if name.endswith('.py'):
        mod = name[:-3]
        assert mod in mods, mod
        text = raw.decode('utf-8-sig')
        mods[mod] = (mods[mod][0], A.module_blob(text, name))   # compiled code that Python actually runs

new_pyz = A.build_pyz(magic, mods, order=order)
pyz_entry.set_data(new_pyz, compress=False)
exe = A.build_exe(x['bootloader'], x['entries'], x['pyver'], x['pylib'])
open(os.path.join(OUT, 'EVA.exe'), 'wb').write(exe)
print('EVA.exe built:', len(exe), 'bytes; changed bundled files:', len(CHANGED))

# release manifest + payload folder (flat names, exactly the set the installer allows)
inner = '/home/claude/eva_v090_inner'
names = sorted(f for f in os.listdir(inner) if os.path.isfile(os.path.join(inner, f)) and f != 'release.json')
payload = {}
for n in names:
    path = os.path.join(OUT, n) if n == 'EVA.exe' else (os.path.join(SRC, n))
    payload[n] = open(path, 'rb').read()
files = {n: hashlib.sha256(b).hexdigest() for n, b in payload.items()}
release = json.dumps({'version': '0.9.7', 'files': dict(sorted(files.items(), key=lambda kv: kv[0].lower()))}, indent=2).encode('utf-8')

now = time.localtime()[:6]
upd = os.path.join(OUT, 'EVA_v0.9.7_UPDATE.zip')
with zipfile.ZipFile(upd, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for n in names + ['release.json']:
        zi = zipfile.ZipInfo(n, date_time=now); zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, release if n == 'release.json' else payload[n])
sha = hashlib.sha256(open(upd, 'rb').read()).hexdigest()
print('UPDATE.zip', os.path.getsize(upd), sha)

# all-in-one wrapper: START_HERE.bat + READ_ME_FIRST.txt + the update zip
outer_dir = '/home/claude/eva_v090_src/EVA_INSTALL'
bat = open(os.path.join(outer_dir, 'START_HERE.bat'), 'rb').read().decode('utf-8')
old_sha = '093420fedb67f05af0eaca6348545f4b8499259de7b0c586a1802a9a5d39b50c'
assert old_sha in bat
bat = bat.replace(old_sha, sha).replace('0.9.0', '0.9.7')
readme = open(os.path.join(outer_dir, 'READ_ME_FIRST.txt'), 'rb').read().decode('utf-8-sig').replace('0.9.0', '0.9.7')
readme = readme.replace('Settings, credentials and AppData are retained.',
    'Settings, credentials and AppData are retained.\nNew in 0.9.7: Google OAuth file found automatically; automatic cleanup of delivered videos after 10 days; EVA Admin health badge, layout-change protection, manual recipients, safe-time sign-in; GitHub updates; hidden EVA Admin connection; Brave opens EVA Admin directly (no blank page) and Google sign-in works there; Brave support (otherwise Chrome); works with the Marassi and Bahrain Admin pages, saves the EVA Admin sign-in,\nsends when only some players have EVA accounts, resumes a recording after a restart,\nand Settings has a "Test Drive & Gmail" button. See README.md inside the app for details.')
allinone = os.path.join(OUT, 'EVA_v0_9_7_ALL_IN_ONE.zip')
with zipfile.ZipFile(allinone, 'w', zipfile.ZIP_STORED) as z:
    for arc, data in (('EVA_INSTALL/EVA_v0.9.7_UPDATE.zip', open(upd, 'rb').read()),
                      ('EVA_INSTALL/START_HERE.bat', bat.encode('utf-8')), ('EVA_INSTALL/READ_ME_FIRST.txt', readme.encode('utf-8'))):
        zi = zipfile.ZipInfo(arc, date_time=now); zi.compress_type = zipfile.ZIP_STORED; z.writestr(zi, data)
print('ALL_IN_ONE', os.path.getsize(allinone), hashlib.sha256(open(allinone, 'rb').read()).hexdigest())
