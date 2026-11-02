"""Capture ownership and validation; never guess the latest file in a shared folder."""
import shutil
import time
from pathlib import Path
from obs_control import ObsUnavailable

EXTENSIONS = {'.mkv', '.mp4', '.mov', '.flv', '.avi', '.webm'}

def snapshot(folder):
    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        raise ObsUnavailable('Set an existing recording folder for each recorder in Settings')
    if shutil.disk_usage(path).free < 2 * 1024**3:
        raise ObsUnavailable(f'Less than 2 GB free in {path}. Free disk space before recording.')
    return {str(p.resolve()): [p.stat().st_size, p.stat().st_mtime_ns] for p in path.iterdir()
            if p.is_file() and p.suffix.casefold() in EXTENSIONS}

def begin_capture(config):
    game, camera = config.get('game_folder', ''), config.get('camera_folder', '')
    if not game or not camera or Path(game).resolve() == Path(camera).resolve():
        raise ObsUnavailable('Use two separate recording folders for Game and Camera, then save them in Settings')
    return {'started': time.time(), 'folders': {'game': str(Path(game).resolve()), 'camera': str(Path(camera).resolve())},
            'before': {'game': snapshot(game), 'camera': snapshot(camera)}}

def resolve_files(capture, outputs):
    result = {}
    for key in ('game', 'camera'):
        folder = Path(capture['folders'][key]).resolve()
        explicit = outputs.get(key)
        candidates = []
        for path in folder.iterdir():
            if not path.is_file() or path.suffix.casefold() not in EXTENSIONS:
                continue
            absolute = str(path.resolve())
            if absolute not in capture['before'][key] and path.stat().st_mtime >= capture['started'] - 2:
                candidates.append(path)
        if explicit:
            path = Path(explicit).resolve()
            if path.parent != folder or str(path) in capture['before'][key] or path not in candidates:
                raise ObsUnavailable(f'{key}: recorder output is outside this session or the configured folder')
        else:
            if len(candidates) != 1:
                raise ObsUnavailable(f'{key}: expected exactly one new video, found {len(candidates)}. Check the recording folder.')
            path = candidates[0]
        previous = path.stat().st_size
        time.sleep(.5)
        if previous < 1024 or path.stat().st_size != previous:
            raise ObsUnavailable(f'{key}: video is empty or still being written. Wait, then retry Stop.')
        with path.open('rb') as handle:
            header = handle.read(12)
        if not (header.startswith(b'\x1a\x45\xdf\xa3') or header[4:8] in {b'ftyp', b'mdat', b'wide'} or
                header.startswith(b'FLV') or header.startswith(b'RIFF')):
            raise ObsUnavailable(f'{key}: unrecognized video container; the session needs review')
        result[key] = str(path)
    if result['game'] == result['camera']:
        raise ObsUnavailable('Game and Camera must be separate video files')
    return result
