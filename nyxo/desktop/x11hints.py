"""Tiny libX11 helper (ctypes) used only in XWayland mode to ask the window
manager to keep NYXO out of the dock/taskbar and alt-tab, and above other
windows. Fails silently if X11 isn't available."""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os

log = logging.getLogger("nyxo.x11")

_lib = None
_err_handler = None   # keep a reference so it isn't garbage-collected

_XErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


def _on_x_error(_dpy, _ev) -> int:
    # Xlib's default handler calls exit(); a stale window id must never kill NYXO.
    log.debug("ignored X11 error")
    return 0


def usable() -> bool:
    """Only touch X11 when Qt itself is running on the xcb platform."""
    try:
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.platformName() == "xcb" and bool(os.environ.get("DISPLAY"))
    except Exception:  # noqa: BLE001
        return False


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong),
                ("message_type", ctypes.c_ulong), ("format", ctypes.c_int),
                ("data", ctypes.c_long * 5)]


class _XEvent(ctypes.Union):
    _fields_ = [("xclient", _XClientMessageEvent), ("pad", ctypes.c_long * 24)]


def _x11():
    global _lib
    if _lib is None:
        name = ctypes.util.find_library("X11")
        if not name:
            return None
        _lib = ctypes.cdll.LoadLibrary(name)
        global _err_handler
        _err_handler = _XErrorHandler(_on_x_error)
        _lib.XSetErrorHandler.restype = ctypes.c_void_p
        _lib.XSetErrorHandler.argtypes = [_XErrorHandler]
        _lib.XSetErrorHandler(_err_handler)
        _lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        _lib.XOpenDisplay.restype = ctypes.c_void_p
        _lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        _lib.XInternAtom.restype = ctypes.c_ulong
        _lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        _lib.XDefaultRootWindow.restype = ctypes.c_ulong
        _lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        _lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                                    ctypes.POINTER(_XEvent)]
        _lib.XFlush.argtypes = [ctypes.c_void_p]
        _lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        _lib.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        _lib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long, ctypes.c_int,
            ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
        _lib.XGetAtomName.restype = ctypes.c_void_p
        _lib.XGetAtomName.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        _lib.XFree.argtypes = [ctypes.c_void_p]
    return _lib


def set_wm_state(win_id: int, *states: str, action: int = 1) -> bool:
    if not usable():
        return False
    try:
        x = _x11()
        if x is None:
            return False
        dpy = x.XOpenDisplay(None)
        if not dpy:
            return False
        try:
            wm_state = x.XInternAtom(dpy, b"_NET_WM_STATE", 0)
            root = x.XDefaultRootWindow(dpy)
            # _NET_WM_STATE takes at most two properties per message
            for i in range(0, len(states), 2):
                pair = states[i:i + 2]
                ev = _XEvent()
                ev.xclient.type = 33          # ClientMessage
                ev.xclient.send_event = 1
                ev.xclient.window = win_id
                ev.xclient.message_type = wm_state
                ev.xclient.format = 32
                ev.xclient.data[0] = action   # 1 = _NET_WM_STATE_ADD, 0 = REMOVE
                ev.xclient.data[1] = x.XInternAtom(dpy, pair[0].encode(), 0)
                ev.xclient.data[2] = x.XInternAtom(dpy, pair[1].encode(), 0) if len(pair) > 1 else 0
                ev.xclient.data[3] = 1        # source: application
                mask = (1 << 20) | (1 << 19)  # SubstructureRedirect | SubstructureNotify
                x.XSendEvent(dpy, root, 0, mask, ctypes.byref(ev))
            x.XFlush(dpy)
        finally:
            x.XCloseDisplay(dpy)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("x11 hint failed: %s", e)
        return False


XA_ATOM = 4
XA_CARDINAL = 6


def _with_display(fn):
    if not usable():
        return None
    try:
        x = _x11()
        if x is None:
            return None
        dpy = x.XOpenDisplay(None)
        if not dpy:
            return None
        try:
            return fn(x, dpy)
        finally:
            x.XCloseDisplay(dpy)
    except Exception as e:  # noqa: BLE001
        log.debug("x11 call failed: %s", e)
        return None


def set_window_type(win_id: int, type_name: str) -> bool:
    """Replace _NET_WM_WINDOW_TYPE. Mutter re-reads it on change."""
    def run(x, dpy):
        prop = x.XInternAtom(dpy, b"_NET_WM_WINDOW_TYPE", 0)
        val = (ctypes.c_ulong * 1)(x.XInternAtom(dpy, type_name.encode(), 0))
        x.XChangeProperty(dpy, win_id, prop, XA_ATOM, 32, 0, ctypes.cast(val, ctypes.c_void_p), 1)
        x.XSync(dpy, 0)          # surface any error now, while our handler is in place
        return True
    return bool(_with_display(run))


def set_all_workspaces(win_id: int) -> bool:
    def run(x, dpy):
        ev = _XEvent()
        ev.xclient.type = 33
        ev.xclient.send_event = 1
        ev.xclient.window = win_id
        ev.xclient.message_type = x.XInternAtom(dpy, b"_NET_WM_DESKTOP", 0)
        ev.xclient.format = 32
        ev.xclient.data[0] = 0xFFFFFFFF
        ev.xclient.data[1] = 1
        x.XSendEvent(dpy, x.XDefaultRootWindow(dpy), 0, (1 << 20) | (1 << 19), ctypes.byref(ev))
        x.XFlush(dpy)
        return True
    return bool(_with_display(run))


def get_atom_list(win_id: int, prop_name: str) -> list[str]:
    """Read an ATOM[] property (for diagnostics/tests)."""
    def run(x, dpy):
        prop = x.XInternAtom(dpy, prop_name.encode(), 0)
        at, fmt = ctypes.c_ulong(), ctypes.c_int()
        n, after = ctypes.c_ulong(), ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        x.XGetWindowProperty(dpy, win_id, prop, 0, 64, 0, XA_ATOM, ctypes.byref(at), ctypes.byref(fmt),
                             ctypes.byref(n), ctypes.byref(after), ctypes.byref(data))
        names = []
        if data and n.value:
            arr = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
            for i in range(n.value):
                ptr = x.XGetAtomName(dpy, arr[i])
                names.append(ctypes.string_at(ptr).decode())
                x.XFree(ptr)
            x.XFree(data)
        return names
    return _with_display(run) or []


def make_companion(win_id: int, desktop_layer: bool = True) -> bool:
    """Keep NYXO visible: above windows, out of the dock/alt-tab, on every
    workspace — and (desktop_layer) as a DOCK-type window, which GNOME keeps
    visible even during "Show Desktop" and never minimises."""
    ok = set_wm_state(win_id, "_NET_WM_STATE_SKIP_TASKBAR", "_NET_WM_STATE_SKIP_PAGER",
                      "_NET_WM_STATE_ABOVE", "_NET_WM_STATE_STICKY")
    if desktop_layer:
        set_window_type(win_id, "_NET_WM_WINDOW_TYPE_DOCK")
    set_all_workspaces(win_id)
    # never show a "NYXO is ready" attention banner
    set_wm_state(win_id, "_NET_WM_STATE_DEMANDS_ATTENTION", action=0)
    return ok
