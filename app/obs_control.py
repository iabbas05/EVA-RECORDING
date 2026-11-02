"""OBS Studio recording adapter and a safe simulator for local testing."""

from __future__ import annotations

import os
import threading

def attributes(response):
    """obsws-python attrs() returns names, not a mapping."""
    if isinstance(response, dict):
        return response
    names = response.attrs() if hasattr(response, 'attrs') else vars(response)
    return {name: getattr(response, name) for name in names}


class ObsUnavailable(RuntimeError):
    pass


class UnavailableRecorder:
    """Keeps the app open when a selected recorder still needs configuration."""

    def __init__(self, mode: str, error: str):
        self.mode = mode
        self.error = error

    def status(self):
        return {"connected": False, "recording": False, "mode": self.mode,
                "output_path": None, "error": self.error}

    def start(self):
        raise ObsUnavailable(self.error)

    def stop(self):
        raise ObsUnavailable(self.error)


class SimulatedObs:
    def __init__(self):
        self.recording = False
        self.output_path = None

    def status(self):
        return {"connected": True, "recording": self.recording, "mode": "simulation", "output_path": self.output_path}

    def start(self):
        if self.recording:
            raise ObsUnavailable("Simulation is already recording")
        self.recording = True
        self.output_path = "SIMULATION_ONLY"
        return self.status()

    def stop(self):
        if not self.recording:
            raise ObsUnavailable("Simulation is not recording")
        self.recording = False
        self.output_path = "SIMULATION_ONLY"
        return self.status()


class ManualRecorder:
    """Tracks the session in the app while the operator controls Streamlabs manually."""

    def __init__(self):
        self.recording = False

    def status(self):
        return {"connected": True, "recording": self.recording, "mode": "manual",
                "output_path": None, "note": "Start and stop video directly in Streamlabs"}

    def start(self):
        if self.recording:
            raise ObsUnavailable("A manually tracked session is already active")
        self.recording = True
        return self.status()

    def stop(self):
        if not self.recording:
            raise ObsUnavailable("No manually tracked session is active")
        self.recording = False
        return self.status()


class ObsWebSocket:
    """Connect to OBS Studio's built-in obs-websocket v5 service."""

    def __init__(self, host: str, port: int, password: str):
        try:
            import obsws_python as obs
        except ImportError as exc:
            raise ObsUnavailable("Install dependencies from requirements.txt to use OBS mode") from exc
        self._obs = obs
        self.host = host
        self.port = port
        self.password = password
        self.client = None
        self.lock = threading.RLock()
        self.last_output_path = None

    def _client(self):
        if self.client is None:
            self.client = self._obs.ReqClient(host=self.host, port=self.port, password=self.password, timeout=4)
            self.client.get_version()
        return self.client

    def status(self):
        try:
            response = self._client().get_record_status()
            attrs = attributes(response)
            return {
                "connected": True,
                "recording": bool(attrs.get("output_active", False)),
                "mode": "obs",
                "output_path": self.last_output_path,
                "paused": bool(attrs.get('output_paused', False)),
            }
        except Exception as exc:
            self.client = None
            return {"connected": False, "recording": False, "mode": "obs",
                    "output_path": None, "error": f"OBS status request failed: {exc}"}


    def preview_jpeg(self, width=360, height=203, quality=55):
        """Return a small JPEG of the active OBS program scene for the in-app live monitor."""
        import base64
        try:
            client = self._client()
            scene = client.get_current_program_scene()
            attrs = attributes(scene)
            name = attrs.get("current_program_scene_name") or attrs.get("scene_name")
            if not name:
                raise ObsUnavailable("OBS program scene is unavailable")
            shot = client.get_source_screenshot(name, "jpg", int(width), int(height), int(quality))
            data = attributes(shot)
            uri = data.get("image_data")
            if not uri:
                raise ObsUnavailable("OBS did not return a preview image")
            return base64.b64decode(uri.split(",",1)[1] if "," in uri else uri)
        except Exception as exc:
            raise ObsUnavailable(f"OBS preview failed: {exc}") from exc

    def start(self):
        try:
            self.last_output_path = None
            self._client().start_record()
            return self.status()
        except Exception as exc:
            raise ObsUnavailable(f"OBS could not start recording: {exc}") from exc

    def stop(self):
        try:
            current = self.status()
            if current.get('connected') and not current.get('recording'):
                return current
            response = self._client().stop_record()
            attrs = attributes(response)
            self.last_output_path = attrs.get('output_path')
            status = self.status()
            status["output_path"] = attrs.get("output_path") or status.get("output_path")
            return status
        except Exception as exc:
            raise ObsUnavailable(f"OBS could not stop recording: {exc}") from exc


def build_obs(password: str | None = None):
    mode = os.getenv("EVA_MANAGER_MODE", "simulation").strip().lower()
    if mode in {"simulation", "sim", "test"}:
        return SimulatedObs()
    if mode == "manual":
        return ManualRecorder()
    if mode not in {"obs", "dual"}:
        raise ObsUnavailable("EVA_MANAGER_MODE must be simulation or obs")
    host = os.getenv("OBS_WS_HOST", "127.0.0.1")
    port = int(os.getenv("OBS_WS_PORT", "4455"))
    if password is None:
        raise ObsUnavailable("OBS WebSocket password was not entered")
    return ObsWebSocket(host, port, password)

def _serialized(method):
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call

for _method in ('status', 'preview_jpeg', 'start', 'stop'):
    setattr(ObsWebSocket, _method, _serialized(getattr(ObsWebSocket, _method)))
