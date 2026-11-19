"""NYXO entry point.

    nyxo              start NYXO (or show the running instance)
    nyxo --chat       open the chat window      nyxo --listen   push-to-talk
    nyxo --hide/--show/--toggle                 nyxo --settings
    nyxo --quit       quit the running instance nyxo --status   print status
    nyxo --service    (used by the systemd unit) run in the foreground
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from .core import brand


def _pick_display_mode(db_mode: str, forced: str | None) -> bool:
    """Set QT_QPA_PLATFORM before Qt starts. Returns True for native Wayland."""
    mode = forced or db_mode or "xwayland"
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    has_x = bool(os.environ.get("DISPLAY"))
    if os.environ.get("QT_QPA_PLATFORM") in ("offscreen", "minimal"):
        return False
    if wayland and mode == "xwayland" and has_x:
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        return False
    if wayland:
        os.environ["QT_QPA_PLATFORM"] = "wayland"
        return True
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nyxo", description=f"{brand.NAME} — Your desktop. Your companion. Your AI.")
    g = ap.add_mutually_exclusive_group()
    for flag in ("chat", "listen", "show", "hide", "toggle", "settings", "quit", "status"):
        g.add_argument(f"--{flag}", action="store_true")
    ap.add_argument("--service", action="store_true", help="run in foreground under systemd")
    ap.add_argument("--mode", choices=["xwayland", "wayland"], help="override display mode once")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    from .core import logging_setup, paths
    paths.ensure_dirs()
    log = logging_setup.setup(args.verbose)

    if args.status:
        from .startup import manager as startup
        from .core.single_instance import InstanceLock
        lock = InstanceLock()
        running = not lock.acquire()
        lock.release()
        st = startup.status()
        print(f"NYXO running: {running}")
        for k, v in st.items():
            print(f"  {k}: {v}")
        print(f"  logs: {paths.log_dir() / 'nyxo.log'}")
        return 0

    cmd = next((f for f in ("chat", "listen", "show", "hide", "toggle", "settings", "quit")
                if getattr(args, f)), None)

    from .core.single_instance import InstanceLock, send_command
    lock = InstanceLock()
    if not lock.acquire():
        from PySide6.QtCore import QCoreApplication
        _qc = QCoreApplication(sys.argv[:1])
        ok = send_command(cmd or "show", retry_s=8.0)
        if not ok:
            log.error(f"{brand.NAME} is running but did not answer (see logs)")
        return 0 if ok else 1
    if cmd in ("quit", "hide"):
        lock.release()
        if cmd == "quit":
            # A crash restart may be pending (systemd waits RestartSec before
            # relaunching). Cancel it so "quit" really means quit.
            from .startup import manager as startup
            if startup.unit_path().exists() and startup.stop_pending():
                print(f"Cancelled a pending {brand.NAME} restart.")
                return 0
        print(f"{brand.NAME} isn't running.")
        return 0

    # Prefer the supervised systemd service when it is installed and enabled,
    # so manual launches still get crash-restart and journald logs.
    if not args.service and not args.mode and os.environ.get("NYXO_NO_SYSTEMD") != "1":
        from .startup import manager as startup
        if startup.is_installed() and startup.running_from_install() and startup.unit_path().exists():
            lock.release()
            if startup.start():
                if cmd:
                    import time
                    from PySide6.QtCore import QCoreApplication
                    _qc = QCoreApplication(sys.argv[:1])
                    for _ in range(40):
                        time.sleep(0.25)
                        if send_command(cmd, 300):
                            break
                return 0
            if not lock.acquire():
                return 0

    from .memory.db import Database
    db = Database(paths.db_path())
    brand.set_name(db.get_setting("assistant_name", brand.DEFAULT))
    native = _pick_display_mode(db.get_setting("display_mode", "xwayland"), args.mode)
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from . import APP_ID
    from .core.app import RESTART_CODE, NyxoApp

    QApplication.setApplicationName("nyxo")
    QApplication.setApplicationDisplayName(f"{brand.NAME}")
    QApplication.setDesktopFileName(APP_ID)
    qapp = QApplication(sys.argv[:1])
    qapp.setQuitOnLastWindowClosed(False)

    app = NyxoApp(qapp, db, native)
    signal.signal(signal.SIGTERM, lambda *_: QTimer.singleShot(0, app.quit))
    signal.signal(signal.SIGINT, lambda *_: QTimer.singleShot(0, app.quit))
    pump = QTimer(interval=500, timeout=lambda: None)   # let Python see signals
    pump.start()

    sys.excepthook = lambda t, v, tb: log.critical("unhandled error", exc_info=(t, v, tb))
    app.start()
    if cmd:
        QTimer.singleShot(800, lambda: app.handle_command(cmd))
    code = qapp.exec()
    log.info("event loop ended with %s", code)
    db.close()
    lock.release()
    if code == RESTART_CODE:
        log.info(f"restarting {brand.NAME}")
        logging.shutdown()
        os.execv(sys.executable, [sys.executable, "-m", "nyxo", *(sys.argv[1:] if "--service" in sys.argv else [])])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
