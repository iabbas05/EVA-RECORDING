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

BOTH = ('game', 'camera')


def keys_for(mode, layout):
    """Which of the two videos (game / camera) a record mode produces. In the 'streamlabs_game' layout Streamlabs records the
    game and OBS the camera; in 'obs_game' it is the other way round."""
    streamlabs = 'game' if layout == 'streamlabs_game' else 'camera'
    obs = 'camera' if streamlabs == 'game' else 'game'
    try:
        return {'both': BOTH, 'streamlabs': (streamlabs,), 'obs': (obs,)}[mode]
    except KeyError:
        raise ObsUnavailable('Choose Streamlabs only, OBS only, or both') from None


def begin_capture(config, keys=BOTH):
    folders = {'game': config.get('game_folder', ''), 'camera': config.get('camera_folder', '')}
    if any(not folders[k] for k in keys) or (len(keys) == 2 and Path(folders['game']).resolve() == Path(folders['camera']).resolve()):
        raise ObsUnavailable('Use two separate recording folders for Game and Camera, then save them in Settings')
    return {'started': time.time(), 'keys': list(keys), 'folders': {k: str(Path(folders[k]).resolve()) for k in keys},
            'before': {k: snapshot(folders[k]) for k in keys}}

def resolve_files(capture, outputs):
    result = {'game': None, 'camera': None}
    for key in capture.get('keys') or BOTH:
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
    if result['game'] and result['game'] == result['camera']:
        raise ObsUnavailable('Game and Camera must be separate video files')
    return result
