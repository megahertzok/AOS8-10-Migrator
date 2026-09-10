"""Entry point that makes the proxy agent visibly running: a system tray/menu-bar icon
(macOS and Windows) plus a quick-access menu, instead of a silent background process.

Falls back to plain console mode automatically if no tray/display is available (e.g. a
headless jump box), or if `proxy_agent.tray_icon: false` is set in config.yaml.
"""

import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from app import app, config, debug_log

LOG_VIEWER_PATH = Path(__file__).parent / "log_viewer.py"


def _run_flask():
    agent_config = config.get("proxy_agent", {})
    app.run(host=agent_config.get("host", "127.0.0.1"), port=agent_config.get("port", 8765), debug=False, use_reloader=False)


def _brand_icon_image():
    """A simple brand-green circle -- no external asset needed, keeps the launcher
    dependency-free beyond pystray/Pillow themselves."""
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill=(1, 169, 130, 255))  # HPE Green #01A982
    draw.polygon(
        [(20, 44), (32, 16), (44, 44), (36, 44), (32, 34), (28, 44)], fill=(255, 255, 255, 255)
    )
    return img


def run_with_tray():
    import PIL.Image

    # pystray 0.19.x's macOS backend references PIL.Image.ANTIALIAS, which Pillow >=10
    # removed in favor of Image.Resampling.LANCZOS (ANTIALIAS was just an alias for it).
    # Standard compatibility shim -- avoids pinning an old, unmaintained Pillow.
    if not hasattr(PIL.Image, "ANTIALIAS"):
        PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS

    import pystray

    agent_config = config.get("proxy_agent", {})
    host, port = agent_config.get("host", "127.0.0.1"), agent_config.get("port", 8765)
    gui_url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/"

    flask_thread = threading.Thread(target=_run_flask, daemon=True)
    flask_thread.start()

    def open_gui(icon, item):
        webbrowser.open(gui_url)

    def view_logs(icon, item):
        # A separate process, not a thread: on macOS both pystray and Tkinter want the
        # main thread for their own event loop, so they can't share this process.
        subprocess.Popen([sys.executable, str(LOG_VIEWER_PATH)])

    def toggle_debug(icon, item):
        debug_log.enabled = not debug_log.enabled
        icon.update_menu()

    def quit_app(icon, item):
        icon.stop()
        import os

        os._exit(0)  # the Flask thread is a daemon; a hard exit is the simplest clean stop

    icon = pystray.Icon(
        "aos8-10-migrator",
        _brand_icon_image(),
        f"AOS8 → AOS10 Migrator — running on {host}:{port}",
        menu=pystray.Menu(
            pystray.MenuItem("AOS8 → AOS10 Migrator — Running", None, enabled=False),
            pystray.MenuItem("Open GUI", open_gui, default=True),
            pystray.MenuItem("View Logs", view_logs),
            pystray.MenuItem("Debug mode (log every REST call / SSH command)", toggle_debug, checked=lambda item: debug_log.enabled),
            pystray.MenuItem("Quit", quit_app),
        ),
    )
    print(f"AOS8 -> AOS10 Migrator proxy agent running. Look for its icon in your system tray / menu bar.\nGUI: {gui_url}\nLog file: {debug_log.LOG_PATH}")
    icon.run()


def run_headless():
    print("Tray icon unavailable (no display, or proxy_agent.tray_icon: false) -- running in console-only mode.")
    agent_config = config.get("proxy_agent", {})
    print(f"GUI: http://{agent_config.get('host', '127.0.0.1')}:{agent_config.get('port', 8765)}/")
    print(f"Log file: {debug_log.LOG_PATH}  (tail it with: tail -f {debug_log.LOG_PATH})")
    print("If this machine does have a display, you can still open the log viewer window separately:")
    print(f"  python3 {LOG_VIEWER_PATH}")
    print("Press Ctrl+C to stop.")
    _run_flask()


if __name__ == "__main__":
    if not config.get("proxy_agent", {}).get("tray_icon", True):
        run_headless()
    else:
        try:
            run_with_tray()
        except Exception as exc:  # noqa: BLE001 -- any tray/display failure falls back to headless
            print(f"Could not start the tray icon ({exc}); falling back to console-only mode.")
            run_headless()
