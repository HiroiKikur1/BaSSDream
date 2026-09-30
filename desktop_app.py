import webview
import threading
import time
import urllib.request
import subprocess
import sys
import os

APP_URL = 'http://localhost:8080'
HEALTH_URL = 'http://localhost:8080/api/songs'

def is_backend_running():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1) as response:
            return response.status == 200
    except Exception:
        return False

def ensure_backend():
    if is_backend_running():
        return None
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'backend'))
    app_py = os.path.join(backend_dir, 'app.py')
    pythonw = r'C:\Python314\pythonw.exe'
    if not os.path.exists(pythonw):
        pythonw = 'pythonw'
    proc = subprocess.Popen([pythonw, app_py], cwd=backend_dir, creationflags=0x08000000)
    for _ in range(30):
        time.sleep(0.3)
        if is_backend_running():
            break
    return proc

def main():
    proc = ensure_backend()
    window = webview.create_window(
        title='BassStation 2.0',
        url=APP_URL,
        width=1440,
        height=900,
        min_size=(1200, 750),
        text_select=True,
    )
    try:
        webview.start(gui='edgechromium', debug=False)
    finally:
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass

if __name__ == '__main__':
    main()
