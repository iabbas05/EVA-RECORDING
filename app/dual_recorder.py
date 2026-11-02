"""Both recorders must positively acknowledge every transition."""
import threading
from concurrent.futures import ThreadPoolExecutor
from obs_control import ObsUnavailable

class DualRecorder:
    def __init__(self, obs, streamlabs, layout='obs_game'):
        if layout not in {'obs_game', 'streamlabs_game'}:
            raise ValueError('Unknown recorder layout')
        self.obs, self.streamlabs = obs, streamlabs
        self.layout = layout
        self.game_recorder, self.camera_recorder = (streamlabs, obs) if layout == 'streamlabs_game' else (obs, streamlabs)
        self.lock = threading.RLock()
        self.outputs = {'game': None, 'camera': None}

    def _parallel(self, operation):
        results, errors = {}, {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = {key: pool.submit(getattr(recorder, operation)) for key, recorder in
                    (('game', self.game_recorder), ('camera', self.camera_recorder))}
            for key, job in jobs.items():
                try:
                    results[key] = job.result()
                except Exception as exc:
                    errors[key] = str(exc)
        return results, errors

    def status(self):
        with self.lock:
            results, errors = self._parallel('status')
            for key in errors:
                results[key] = {'connected': False, 'recording': False, 'error': errors[key]}
            game, camera = results['game'], results['camera']
            connected = all(r.get('connected') for r in results.values())
            recording = any(r.get('recording') for r in results.values())
            synchronized = connected and game.get('recording') == camera.get('recording')
            issues = [f'{key}: {r.get("error", "Disconnected")}' for key, r in results.items() if not r.get('connected')]
            if connected and not synchronized:
                issues.append('Only one recorder is recording. Stop and review this session.')
            if any(r.get('paused') for r in results.values()):
                issues.append('A recorder is paused. This session is incomplete.')
                synchronized = False
            return {'connected': connected, 'recording': recording, 'synchronized': synchronized,
                    'mode': 'dual', 'layout': self.layout, 'game': game, 'camera': camera,
                    'obs': camera if self.layout == 'streamlabs_game' else game,
                    'streamlabs': game if self.layout == 'streamlabs_game' else camera,
                    'output_path': self.outputs['game'],
                    'output_paths': dict(self.outputs), 'error': ' | '.join(issues) or None}

    def start(self):
        with self.lock:
            before = self.status()
            if not before['connected'] or before['recording']:
                raise ObsUnavailable(before['error'] or 'A recorder is already recording outside EVA')
            self.outputs = {'game': None, 'camera': None}
            _, errors = self._parallel('start')
            state = self.status()
            if errors or not state['connected'] or not state['synchronized'] or not state['recording']:
                _, rollback = self._parallel('stop')
                raise ObsUnavailable('Dual recording did not start: ' + str(errors or state['error']) +
                                     ('. Stop recovery needs attention: ' + str(rollback) if rollback else '. Both stopped.'))
            return state

    def stop(self):
        with self.lock:
            results, errors = self._parallel('stop')
            for key, result in results.items():
                self.outputs[key] = result.get('output_path') or self.outputs[key]
            state = self.status()
            if errors or not state['connected'] or state['recording']:
                raise ObsUnavailable('Could not confirm both recorders stopped: ' + str(errors or state['error']))
            return state
