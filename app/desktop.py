"""EVA Windows desktop shell, with isolated verification and LAN server modes."""
import argparse
import json
import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path
from app_paths import DATA

def configure_logging():
    directory = DATA / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(directory / 'eva.log', maxBytes=2*1024**2, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.getLogger('eva').addHandler(handler)
    logging.getLogger('eva').setLevel(logging.INFO)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--server-only', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if args.self_test:
        from self_test import run
        result = run()
        if args.output:
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(result, ensure_ascii=False))
        return 0 if result['ok'] else 1
    configure_logging()
    # A socket bind reserves the instance before importing modules that touch session state.
    from http.server import ThreadingHTTPServer
    from http.server import BaseHTTPRequestHandler
    try:
        server = ThreadingHTTPServer(('0.0.0.0', args.port), BaseHTTPRequestHandler)
    except OSError:
        if args.server_only:
            return 2
        import webview
        webview.create_window('EVA', f'http://127.0.0.1:{args.port}', width=1280, height=860, min_size=(800,600), text_select=True, easy_drag=False)
        webview.start(private_mode=False, gui='edgechromium')
        return 0
    import manager
    server.RequestHandlerClass = manager.Handler
    manager.start_background()
    if args.server_only:
        server.serve_forever()
        return 0
    import webview
    threading.Thread(target=server.serve_forever, daemon=True, name='eva-http').start()
    window = webview.create_window('EVA', f'http://127.0.0.1:{args.port}', width=1280, height=860,
                                  min_size=(800,600), background_color='#f4f5fa', text_select=True, easy_drag=False)
    def closing():
        if manager.SERVICE.active_id or manager.QUEUE.busy():
            # Keep the window and server alive; recordings must be stopped deliberately.
            try:
                window.evaluate_js("document.getElementById('recordState').textContent='Stop recording and wait for delivery before closing EVA.'")
            except Exception:
                logging.getLogger('eva').warning('Window closure prevented during active recording/delivery')
            return False
    window.events.closing += closing
    try:
        webview.start(debug=False, private_mode=False, gui='edgechromium', storage_path=str(DATA / 'DesktopProfile'))
    finally:
        manager.QUEUE.closed.set()
        server.shutdown()
        server.server_close()
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        logging.getLogger('eva').exception('EVA failed to start')
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, 'EVA could not start. See Settings diagnostics or the EVA logs folder.', 'EVA', 0x10)
        raise
