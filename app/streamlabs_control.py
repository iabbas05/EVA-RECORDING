"""Local Streamlabs Desktop recording adapter using its Remote Control API."""

from __future__ import annotations

import json
import secrets
import time
import threading

from obs_control import ObsUnavailable


class StreamlabsRecorder:
    def __init__(self, host: str, port: int, token: str):
        try:
            import websocket
        except ImportError as exc:
            raise ObsUnavailable("Install dependencies from requirements.txt to use Streamlabs mode") from exc
        self.websocket = websocket
        self.host, self.port, self.token = host, port, token
        self.lock = threading.RLock()

    def _request(self, ws, method: str, resource: str, args=None):
        request_id = secrets.randbelow(1_000_000_000)
        packet = {"jsonrpc": "2.0", "id": request_id, "method": method,
                  "params": {"resource": resource, "args": list(args or [])}}
        ws.send(json.dumps([json.dumps(packet, separators=(",", ":"))]))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            frame = ws.recv()
            if frame == "h":
                continue
            if not frame.startswith("a"):
                continue
            for message in json.loads(frame[1:]):
                result = json.loads(message)
                if result.get("id") == request_id:
                    if "error" in result:
                        raise ObsUnavailable(f"Streamlabs API error: {result['error']}")
                    return result.get("result")
        raise ObsUnavailable(f"Streamlabs did not answer {resource}.{method}")

    def _connect(self):
        path_id = secrets.token_hex(4)
        session_id = secrets.token_hex(4)
        url = f"ws://{self.host}:{self.port}/api/{path_id}/{session_id}/websocket"
        try:
            ws = self.websocket.create_connection(url, timeout=5, origin="http://localhost")
            ws.settimeout(8)
            opened = ws.recv()
            if opened != "o":
                ws.close()
                raise ObsUnavailable("Streamlabs Remote Control did not open a SockJS session")
            self._request(ws, "auth", "TcpServerService", [self.token])
            return ws
        except ObsUnavailable:
            raise
        except Exception as exc:
            raise ObsUnavailable(f"Could not connect to Streamlabs Remote Control: {exc}") from exc

    @staticmethod
    def _recording_state(model):
        def scan(value):
            if isinstance(value, dict):
                status = value.get("recordingStatus")
                if isinstance(status, str):
                    return status.casefold()
                for item in value.values():
                    found = scan(item)
                    if found:
                        return found
            elif isinstance(value, list):
                for item in value:
                    found = scan(item)
                    if found:
                        return found
            return None
        return scan(model)

    def _model(self):
        ws = self._connect()
        try:
            return self._request(ws, "getModel", "StreamingService")
        finally:
            ws.close()

    def status(self):
        try:
            model = self._model()
            state = self._recording_state(model)
            if state is None:
                raise ObsUnavailable("Streamlabs API response did not include recording status")
            if state not in {'offline', 'recording', 'starting', 'stopping', 'writing', 'wrote'}:
                raise ObsUnavailable('Unknown Streamlabs recording state: ' + state)
            recording = state not in {'offline', 'wrote'}
            return {"connected": True, "recording": recording, "mode": "streamlabs",
                    "output_path": None, "recording_state": state}
        except Exception as exc:
            return {"connected": False, "recording": False, "mode": "streamlabs",
                    "output_path": None, "error": str(exc)}

    def _set_recording(self, desired: bool):
        current = self.status()
        if not current["connected"]:
            raise ObsUnavailable(current.get("error", "Streamlabs is not connected"))
        if current.get('recording_state') in ({'recording'} if desired else {'offline', 'wrote'}):
            return current
        if current.get("recording_state") not in {"offline", "recording"}:
            raise ObsUnavailable(f"Streamlabs is currently {current.get('recording_state')}; wait and try again")
        ws = self._connect()
        try:
            # Streamlabs exposes a toggle. Re-read its state immediately before toggling
            # so a stale UI status can never reverse the user's requested action.
            latest = self._request(ws, "getModel", "StreamingService")
            state = self._recording_state(latest)
            is_recording = state == "recording"
            if state not in {"offline", "recording"}:
                raise ObsUnavailable(f"Streamlabs is currently {state or 'in an unknown state'}")
            if is_recording != desired:
                self._request(ws, "toggleRecording", "StreamingService")
        finally:
            ws.close()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = self.status()
            if result["connected"] and result.get('recording_state') in ({'recording'} if desired else {'offline', 'wrote'}):
                return result
            time.sleep(0.5)
        raise ObsUnavailable("Streamlabs did not reach the requested recording state")


    def preview_jpeg(self, width=360, height=203, quality=55):
        """Capture only the explicitly configured program projector, never the editor window."""
        try:
            import ctypes, io
            from ctypes import wintypes
            from PIL import Image, ImageStat
            from app_paths import settings
            config = settings()
            title = config.get('streamlabs_projector_title', config.get('camera_projector_title', '')).strip()
            if not title:
                candidates = projector_windows()
                if len(candidates)==1:
                    title = candidates[0]['title']
                else:
                    raise ObsUnavailable('Open a Streamlabs Projector. In Settings choose Find preview to select its window.')
            user32=ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            # Explicit pointer signatures prevent 64-bit Windows handle truncation.
            user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            user32.FindWindowW.restype = wintypes.HWND
            user32.GetDC.argtypes = [wintypes.HWND]; user32.GetDC.restype = wintypes.HDC
            user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
            user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            user32.IsIconic.argtypes = [wintypes.HWND]
            user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
            gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]; gdi32.CreateCompatibleDC.restype = wintypes.HDC
            gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]; gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
            gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]; gdi32.SelectObject.restype = wintypes.HGDIOBJ
            gdi32.GetBitmapBits.argtypes = [wintypes.HBITMAP, wintypes.LONG, ctypes.c_void_p]
            gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]; gdi32.DeleteDC.argtypes = [wintypes.HDC]
            hwnd = user32.FindWindowW(None, title)
            if not hwnd or user32.IsIconic(hwnd):
                raise ObsUnavailable('Streamlabs program projector is closed or minimized. Reopen it on the Streaming PC.')
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            w, h = rect.right, rect.bottom
            if w <= 0 or h <= 0 or w*h > 16_000_000:
                raise ObsUnavailable('Streamlabs program projector size is unavailable')
            dc = user32.GetDC(hwnd); mem = gdi32.CreateCompatibleDC(dc); bmp = gdi32.CreateCompatibleBitmap(dc, w, h)
            old = gdi32.SelectObject(mem, bmp)
            try:
                if not user32.PrintWindow(hwnd, mem, 3):
                    raise ObsUnavailable('Streamlabs program projector capture failed')
                buf = ctypes.create_string_buffer(w*h*4)
                if gdi32.GetBitmapBits(bmp, len(buf), buf) != len(buf):
                    raise ObsUnavailable('Streamlabs program projector did not return an image')
                img = Image.frombuffer('RGB', (w,h), buf.raw, 'raw', 'BGRX', 0, 1)
                if max(ImageStat.Stat(img).mean) < 2:
                    raise ObsUnavailable('Streamlabs program projector returned black pixels. Check its preview on the Streaming PC; the image cannot be verified.')
            finally:
                gdi32.SelectObject(mem, old); gdi32.DeleteObject(bmp); gdi32.DeleteDC(mem); user32.ReleaseDC(hwnd, dc)
            img.thumbnail((int(width),int(height)))
            out=io.BytesIO(); img.convert('RGB').save(out,'JPEG',quality=int(quality),optimize=True)
            return out.getvalue()
        except ObsUnavailable: raise
        except Exception as exc: raise ObsUnavailable(f'Streamlabs preview failed: {exc}') from exc

    def start(self):
        return self._set_recording(True)

    def stop(self):
        return self._set_recording(False)


def build_streamlabs(token: str):
    from os import getenv
    return StreamlabsRecorder(getenv("STREAMLABS_HOST", "127.0.0.1"),
                              int(getenv("STREAMLABS_PORT", "59650")), token)

def _serialized(method):
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call

for _method in ('status', 'start', 'stop'):
    setattr(StreamlabsRecorder, _method, _serialized(getattr(StreamlabsRecorder, _method)))

def projector_windows():
    """Return only Streamlabs-owned projector windows, excluding other applications."""
    import os
    if os.name!='nt':
        return []
    import ctypes
    from ctypes import wintypes
    user32,kernel32 = ctypes.windll.user32,ctypes.windll.kernel32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    kernel32.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel32.OpenProcess.restype=wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
    user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextLengthW.argtypes=[wintypes.HWND]
    user32.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user32.IsWindowVisible.argtypes=[wintypes.HWND]
    user32.IsIconic.argtypes=[wintypes.HWND]
    user32.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
    found=[]
    def inspect(hwnd,_):
        try:
            length=user32.GetWindowTextLengthW(hwnd)
            title=ctypes.create_unicode_buffer(length+1)
            user32.GetWindowTextW(hwnd,title,len(title))
            if 'projector' not in title.value.casefold() or not user32.IsWindowVisible(hwnd):
                return True
            pid=wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            process=kernel32.OpenProcess(0x1000,False,pid.value)
            if not process:
                return True
            try:
                exe=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(exe))
                if kernel32.QueryFullProcessImageNameW(process,0,exe,ctypes.byref(size)) and 'streamlabs' in os.path.basename(exe.value).casefold():
                    found.append({'title':title.value,'minimized':bool(user32.IsIconic(hwnd))})
            finally:
                kernel32.CloseHandle(process)
        except Exception:
            pass
        return True
    user32.EnumWindows(callback_type(inspect),0)
    return found
