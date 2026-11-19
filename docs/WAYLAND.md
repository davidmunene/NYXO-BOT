# NYXO on GNOME Wayland

GNOME's Wayland compositor deliberately doesn't let normal (native Wayland)
windows choose their own position, stay above other windows, or read global
input. NYXO doesn't try to bypass that. It offers two supported modes:

## XWayland mode (default)

Only NYXO's own windows run through XWayland, GNOME's built-in X11
compatibility layer (`QT_QPA_PLATFORM=xcb`). Mutter honours window position
and "keep above" for X11 clients, so NYXO can walk along the bottom of the
screen. Via `_NET_WM_STATE` it asks to stay above other windows and out of the
dock and Alt-Tab. This doesn't weaken security for other apps: XWayland clients
can't read input or pixels from native Wayland windows.

Caveat: on fractional scaling NYXO may look slightly soft (XWayland scaling).

### Always there, even on an empty desktop

NYXO marks its windows as `_NET_WM_WINDOW_TYPE_DOCK` and sticky. GNOME keeps
dock windows visible while you "Show Desktop" or minimise everything, and on
every workspace. A watchdog also restores NYXO if anything minimises or hides it
(unless you chose Hide). You can turn this off in Settings → General.

## Native Wayland mode

Settings → General → Display mode. NYXO lives in a fixed transparent "stage"
window. You place it once by dragging (the compositor performs the move), and
NYXO walks within it. The input region is masked to the robot and the bubble,
so clicks pass through the rest. GNOME may not keep it above other windows.

## Things NYXO deliberately does not do

* Read your keyboard or mouse globally. Idle detection uses GNOME's
  `org.gnome.Mutter.IdleMonitor`, which only reports *how long* you've been idle.
* Grab global hotkeys. The optional shortcut (Super+Alt+N) is a GNOME custom
  keybinding; your existing ones are backed up first.
* Track the cursor across other apps' windows.
* Read notification contents. For announcements, NYXO observes the session
  bus calls `org.freedesktop.Notifications.Notify` and
  `org.gtk.Notifications.AddNotification` with `dbus-monitor`, and reads only
  the first argument (the sending app's name or ID). Titles and bodies are
  skipped unparsed and never logged or stored. Notifications are silent in
  focus mode and when GNOME's Do Not Disturb is on.
