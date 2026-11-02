"""Validate complete prebuilt releases; keep all state outside the replaceable payload."""
import ast
import hashlib
import json
import re
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path
from app_paths import DATA, INSTALL, VERSION

MAX_ARCHIVE = 256 * 1024**2
MAX_UNPACKED = 512 * 1024**2
ALLOWED = { 'EVA.exe', 'app_paths.py', 'manager.py', 'matching.py', 'obs_control.py', 'service.py',
    'storage.py', 'index.html', 'requirements.txt', 'requirements.lock.txt', 'updater.py', 'streamlabs_control.py',
    'eva_portal.py', 'google_delivery.py', 'diagnostics.py', 'version.txt', 'eva.ico', 'desktop.py',
    'dual_recorder.py', 'capture_files.py', 'delivery_queue.py', 'recorder_repair.py', 'self_test.py',
    'install_update.ps1', 'Install_EVA.bat', 'README.md', 'GOOGLE_SETUP.md', 'TEST_CHECKLIST.md', 'release.json', 'media_check.py', 'THIRD_PARTY_NOTICES.md',
    'START_EVA_MANAGER.bat','setup_and_run.ps1','start_streaming_pc.ps1','build_desktop.ps1'}

GITHUB_REPO = 'iabbas05/EVA-RECORDING'
# Releases are published on GitHub: latest.json (version, package_url, sha256) sits in the repository and the
# EVA_v<version>_UPDATE.zip is a release asset. The SHA-256 in latest.json is checked before anything is installed.
DEFAULT_MANIFEST = 'https://raw.githubusercontent.com/' + GITHUB_REPO + '/main/latest.json'


class UpdateError(RuntimeError):
    pass

def version_tuple(value):
    if not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise UpdateError('Version must use major.minor.patch')
    return tuple(map(int, value.split('.')))

def _ssl_context():
    """Windows certificates plus the CA list shipped with EVA (certifi): GitHub downloads work on PCs whose certificate store is incomplete."""
    import ssl
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(certifi.where())
    except Exception:
        pass
    return context


def _open_https(url, limit):
    if not url.startswith('https://'):
        raise UpdateError('Update downloads require HTTPS')
    request = urllib.request.Request(url, headers={'User-Agent': 'EVA/' + VERSION})
    try:
        with urllib.request.urlopen(request, timeout=45, context=_ssl_context()) as response:
            if not response.geturl().startswith('https://'):
                raise UpdateError('Update server redirected to an insecure URL')
            data = response.read(limit + 1)
    except UpdateError:
        raise
    except Exception as exc:
        if 'CERTIFICATE_VERIFY_FAILED' in str(exc):
            raise UpdateError('This PC does not trust the GitHub certificate (antivirus or network HTTPS scanning, or a wrong PC clock). '
                              'Check the PC date and time, or use Browse update ZIP with the file from GitHub Releases.') from exc
        raise
    if len(data) > limit:
        raise UpdateError('Update response exceeds the size limit')
    return data

def check(manifest_url):
    try:
        manifest = json.loads(_open_https(manifest_url, 256_000))
        latest = str(manifest['version'])
        url = str(manifest['package_url'])
        digest = str(manifest['sha256']).lower()
        if not url.startswith('https://') or not re.fullmatch('[0-9a-f]{64}', digest):
            raise UpdateError('Manifest requires an HTTPS package and its SHA-256')
        return {'current_version': VERSION, 'latest_version': latest, 'available': version_tuple(latest) > version_tuple(VERSION),
                'package_url': url, 'sha256': digest}
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError('Could not read the trusted update manifest: ' + str(exc)) from exc

def validate_archive_names(names):
    if len(names) != len(set(names)) or any(n not in ALLOWED or '/' in n or '\\' in n for n in names):
        raise UpdateError('Package contains duplicate or unexpected files')

def validate_package(package):
    path = Path(package)
    if path.is_dir():
        raise UpdateError('A folder was selected. Use Browse to select EVA UPDATE.zip or ALL_IN_ONE.zip.')
    if not path.is_file():
        raise UpdateError('Update file not found. Use Browse to select the downloaded EVA ZIP.')
    if path.stat().st_size > MAX_ARCHIVE:
        raise UpdateError('Update ZIP is too large; the limit is 256 MiB.')
    try:
        with zipfile.ZipFile(path) as archive:
            validate_archive_names(archive.namelist())
            if sum(i.file_size for i in archive.infolist()) > MAX_UNPACKED:
                raise UpdateError('Unpacked update exceeds 512 MiB')
            if any(i.flag_bits & 1 or (i.external_attr >> 16) & 0o170000 == 0o120000 for i in archive.infolist()):
                raise UpdateError('Encrypted files or links are not accepted')
            release = json.loads(archive.read('release.json'))
            version_tuple(release['version'])
            files = release['files']
            if set(files) != set(archive.namelist()) - {'release.json'} or not {'EVA.exe','manager.py','desktop.py','index.html','version.txt','install_update.ps1'}.issubset(files):
                raise UpdateError('Release file list does not match the package')
            if archive.read('version.txt').decode('utf-8-sig').strip() != release['version']:
                raise UpdateError('Packaged version differs from the release manifest')
            for name, digest in files.items():
                content = archive.read(name)
                if hashlib.sha256(content).hexdigest() != digest:
                    raise UpdateError('Package checksum mismatch: ' + name)
                if name.endswith('.py'):
                    ast.parse(content.decode('utf-8-sig'), filename=name)
        return release
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError('Invalid EVA update package: ' + str(exc)) from exc

def stage(manifest_url='', local_package=''):
    try:
        if local_package:
            source = resolve_package(local_package)
            release = validate_package(source)
            latest = release['version']
        else:
            if not manifest_url or 'your-trusted-host' in manifest_url:
                raise UpdateError('Use Browse to choose an EVA update ZIP. No online update source is configured.')
            info = check(manifest_url)
            if not info['available']:
                return {'staged': False, 'message': 'EVA is up to date', **info}
            content = _open_https(info['package_url'], MAX_ARCHIVE)
            if hashlib.sha256(content).hexdigest() != info['sha256']:
                raise UpdateError('Downloaded package does not match the trusted manifest')
            pending = DATA / 'updates'
            pending.mkdir(parents=True, exist_ok=True)
            source = pending / 'download.zip'
            source.write_bytes(content)
            release = validate_package(source)
            latest = release['version']
            if latest != info['latest_version']:
                raise UpdateError('Manifest and packaged versions disagree')
        if version_tuple(latest) <= version_tuple(VERSION):
            return {'staged': False, 'message': 'EVA already has this version or a newer one'}
        pending = DATA / 'updates' / 'pending.zip'
        pending.parent.mkdir(parents=True, exist_ok=True)
        if source != pending:
            shutil.copy2(source, pending)
        return {'staged': True, 'latest_version': latest, 'package_path': str(pending)}
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError('Update staging failed: ' + str(exc)) from exc

def launch_update(package_path, pid):
    # Copy the trusted current worker outside install/resources, so it survives app replacement.
    worker = DATA / 'updates' / 'apply-release.ps1'
    validate_package(package_path)
    with zipfile.ZipFile(package_path) as archive:
        worker.write_bytes(archive.read('install_update.ps1'))
    subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(worker),
                      '-PackagePath',str(package_path),'-Target',str(INSTALL),'-WaitPid',str(pid)],
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

def resolve_package(package):
    """Accept the operator's flat UPDATE ZIP or our three-file all-in-one wrapper."""
    path = Path(package).expanduser().resolve()
    if not path.is_file():
        validate_package(path)
    if path.stat().st_size > MAX_ARCHIVE:
        raise UpdateError('Update ZIP exceeds 256 MiB')
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if 'release.json' in names:
                validate_package(path)
                return path
            updates = [n for n in names if re.fullmatch(r'EVA_INSTALL/EVA_v\d+\.\d+\.\d+_UPDATE\.zip',n)]
            if len(updates)!=1 or set(names)-{updates[0],'EVA_INSTALL/START_HERE.bat','EVA_INSTALL/READ_ME_FIRST.txt'}:
                raise UpdateError('This is not an EVA UPDATE or ALL_IN_ONE ZIP. Do not select SOURCE.zip.')
            info = archive.getinfo(updates[0])
            if info.file_size>MAX_ARCHIVE or info.flag_bits & 1:
                raise UpdateError('Invalid bundled update ZIP')
            destination = DATA/'updates'/'selected-update.zip'
            destination.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(info) as incoming, destination.open('wb') as outgoing:
                shutil.copyfileobj(incoming,outgoing,1024*1024)
        validate_package(destination)
        return destination
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError('Could not read the downloaded ZIP. Download it again.') from exc
