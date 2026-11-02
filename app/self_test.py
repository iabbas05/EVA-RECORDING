"""A packaged release proves its resource version and isolated database migration."""
import ast
import hashlib
import importlib
import tempfile
from pathlib import Path
from app_paths import RESOURCE, VERSION

def run():
    checks = []
    def add(name, action):
        try:
            action()
            checks.append({'name': name, 'ok': True})
        except Exception as exc:
            checks.append({'name': name, 'ok': False, 'error': str(exc)})
    for source in RESOURCE.glob('*.py'):
        add('syntax:' + source.name, lambda p=source: ast.parse(p.read_text(encoding='utf-8-sig'), filename=p.name))
    for module in ('obsws_python','websocket','keyring.backends.Windows','googleapiclient.discovery',
                   'google_auth_oauthlib.flow','google_auth_httplib2','playwright.sync_api','webview','PIL.Image','av'):
        add('import:' + module, lambda m=module: importlib.import_module(m))
    def database():
        from storage import Store
        with tempfile.TemporaryDirectory(prefix='eva-verify-') as directory:
            store = Store(str(Path(directory) / 'test.sqlite3'))
            assert store.healthcheck() == 'ok'
            assert store.jobs() == []
            slot={'session_date':'2026-10-06','start_time':'16:40','end_time':'17:20','game':'Probe'}
            store.save_session_recipients(slot,[{'email':'probe@example.com'}])
            assert store.session_recipients(slot)[0]['email']=='probe@example.com'
    add('database:migration-and-close', database)
    def resources():
        required = ('version.txt','index.html','eva.ico','install_update.ps1','manager.py','desktop.py')
        assert all((RESOURCE / name).is_file() for name in required), 'Required packaged resource missing'
        assert 'EVA-JH' in (RESOURCE / 'index.html').read_text(encoding='utf-8')
        ui=(RESOURCE / 'index.html').read_text(encoding='utf-8')
        assert 'lang="en"' in ui and 'browseUpdate' in ui and 'unassigned:true,auto_send:true' in ui
        assert len(VERSION.split('.')) == 3
    add('resources:version-and-ui', resources)
    def recorder_roles():
        from dual_recorder import DualRecorder
        class Probe:
            def __init__(self, mode): self.mode = mode
            def status(self): return {'connected': True, 'recording': False, 'mode': self.mode}
        obs, streamlabs = Probe('obs'), Probe('streamlabs')
        recorder = DualRecorder(obs, streamlabs, 'streamlabs_game')
        assert recorder.game_recorder is streamlabs and recorder.camera_recorder is obs
        assert recorder.status()['game']['mode'] == 'streamlabs'
        assert recorder.status()['camera']['mode'] == 'obs'
    add('recorders:streamlabs-game-obs-camera', recorder_roles)
    def media():
        import av
        from PIL import Image
        from media_check import inspect_video
        with tempfile.TemporaryDirectory(prefix='eva-media-verify-') as directory:
            path = Path(directory) / 'probe.mp4'
            with av.open(str(path), 'w') as container:
                stream = container.add_stream('mpeg4', rate=10)
                stream.width = stream.height = 64
                stream.pix_fmt = 'yuv420p'
                for i in range(20):
                    frame = av.VideoFrame.from_image(Image.new('RGB', (64,64), (i*10,80,160)))
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            assert inspect_video(path)['duration'] >= 1
    add('media:encode-and-decode-native-libraries', media)
    resource_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in RESOURCE.iterdir()
                       if p.is_file() and p.suffix in {'.py','.html','.ico','.txt','.ps1','.bat','.md'} and p.name != 'release.json'}
    return {'ok': all(c['ok'] for c in checks), 'version': VERSION, 'checks': checks, 'resource_hashes': resource_hashes}
