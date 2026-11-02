import sys, os, ast, json, zipfile, hashlib, marshal, struct, zlib, re, io, tempfile
V=sys.argv[1]; OUT=sys.argv[2]; SRC=sys.argv[3]
sys.path.insert(0,'/home/claude/tools'); import exe_archive as A
upd=f'{OUT}/EVA_v{V}_UPDATE.zip'; allin=f'{OUT}/EVA_v{V.replace(".","_")}_ALL_IN_ONE.zip'
fails=[]
def ok(c,m): print(('PASS ' if c else 'FAIL ')+m); (c or fails.append(m))
ps1=open('/home/claude/eva_v090_inner/install_update.ps1',encoding='utf-8').read()
allowed=set(re.findall(r"'([^']+)'", ps1[ps1.index('$allowed = @('):ps1.index('$stage =')]))
z=zipfile.ZipFile(upd); names=z.namelist(); rel=json.loads(z.read('release.json'))
ok(z.testzip() is None and len(names)==len(set(names)) and set(names)-{'release.json'}<=allowed,f'{len(names)} files, intact, all on the installer allow-list, no new file names')
ok(rel['version']==V and z.read('version.txt').decode().strip()==V and set(rel['files'])==set(names)-{'release.json'},f'release.json/version.txt say {V}; manifest lists exactly the payload')
ok(all(hashlib.sha256(z.read(n)).hexdigest()==h for n,h in rel['files'].items()),'every checksum in release.json matches')
ok(all(ast.parse(z.read(n).decode('utf-8-sig')) is not None for n in names if n.endswith('.py')),'every .py parses')
sys.path.insert(0,'/home/claude/eva_v090_inner'); os.environ['EVA_DATA_DIR']=tempfile.mkdtemp(); import updater
ok(updater.validate_package(upd)['version']==V,'v0.9.0 in-app updater validates the UPDATE zip')
ok(updater.validate_package(updater.resolve_package(allin))['version']==V,'v0.9.0 in-app updater accepts the ALL_IN_ONE zip')
a=zipfile.ZipFile(allin); bat=a.read('EVA_INSTALL/START_HERE.bat').decode(); innerzip=a.read(f'EVA_INSTALL/EVA_v{V}_UPDATE.zip')
ok(hashlib.sha256(innerzip).hexdigest() in bat and innerzip==open(upd,'rb').read() and '0.9.0' not in bat,'START_HERE.bat has the right SHA-256, names 0.9.x only')
print(a.read('EVA_INSTALL/READ_ME_FIRST.txt').decode()[:700])
old=A.read_exe('/home/claude/eva_v090_inner/EVA.exe'); new=A.read_exe(f'{OUT}/EVA.exe')
ok(new['bootloader']==old['bootloader'] and new['pyver']==old['pyver'],'program part of EVA.exe is byte-identical to 0.9.0')
ok([e.name for e in old['entries']]==[e.name for e in new['entries']],'same 1303 entries, same order')
CH={'eva_portal.py','google_delivery.py','service.py','manager.py','diagnostics.py','index.html','version.txt','README.md','TEST_CHECKLIST.md','recorder_repair.py','updater.py','delivery_queue.py','storage.py'}
diff={o.name for o,n in zip(old['entries'],new['entries']) if o.stored!=n.stored}
ok(diff==CH|{'PYZ.pyz'},f'only these entries differ: {sorted(diff)}')
_,_,om=A.read_pyz(next(e for e in old['entries'] if e.typ=='z').data()); _,_,nm=A.read_pyz(next(e for e in new['entries'] if e.typ=='z').data())
cm={n[:-3] for n in CH if n.endswith('.py')}
ok(set(om)==set(nm) and all(om[m]==nm[m] for m in om if m not in cm),f'{len(om)-len(cm)} other compiled modules byte-identical')
for m in sorted(cm):
    src=open(f'{SRC}/{m}.py',encoding='utf-8-sig').read()
    ok(marshal.loads(zlib.decompress(nm[m][1]))==compile(src,m+'.py','exec',dont_inherit=True),f'compiled {m} == {V} source')
data={e.name:e.data() for e in new['entries'] if e.typ=='b' and '\\' not in e.name}
bad=[n for n,d in data.items() if os.path.splitext(n)[1] in {'.py','.html','.ico','.txt','.ps1','.bat','.md'} and rel['files'].get(n)!=hashlib.sha256(d).hexdigest()]
ok(not bad,f'installer self-test comparison of bundled files {bad}')
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
c=CArchiveReader(f'{OUT}/EVA.exe'); tp=tempfile.mktemp(); open(tp,'wb').write(c.extract('PYZ.pyz')); ZlibArchiveReader(tp,0).extract('eva_portal')
ok(len(c.toc)>1200,"PyInstaller's own readers open it")
base=zipfile.ZipFile(io.BytesIO(next(e for e in new['entries'] if e.name=='base_library.zip').data())).namelist()
def imports(s):
    o=set()
    for n in ast.walk(ast.parse(s)):
        if isinstance(n,ast.Import): o|={x.name for x in n.names}
        elif isinstance(n,ast.ImportFrom) and n.module and n.level==0: o.add(n.module)
    return o
miss=[]
for m in cm:
    for i in sorted(imports(open(f'{SRC}/{m}.py',encoding='utf-8-sig').read())-imports(open(f'/home/claude/eva_v090_inner/{m}.py',encoding='utf-8-sig').read())):
        top=i.split('.')[0]
        if not (i in nm or top+'.pyc' in base or i.replace('.','/')+'.pyc' in base or i.replace('.','/')+'/__init__.pyc' in base or top in sys.builtin_module_names or top in ('app_paths','eva_portal','google_delivery','pathlib')): miss.append((m,i))
ok(not miss,f'every new import is already inside EVA.exe {miss}')
print('ALL PASSED' if not fails else 'FAILURES: '+str(fails)); sys.exit(1 if fails else 0)
