// NYXO Helper — tells NYXO whether any monitor is showing a full-screen window.
// Only a boolean is exposed (io.nyxo.ShellHelper.InFullscreen / FullscreenChanged).
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `
<node>
  <interface name="io.nyxo.ShellHelper">
    <method name="InFullscreen"><arg type="b" direction="out" name="fullscreen"/></method>
    <signal name="FullscreenChanged"><arg type="b" name="fullscreen"/></signal>
  </interface>
</node>`;

export default class NyxoHelperExtension extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/io/nyxo/ShellHelper');
        this._nameId = Gio.bus_own_name_on_connection(Gio.DBus.session, 'io.nyxo.ShellHelper',
            Gio.BusNameOwnerFlags.NONE, null, null);
        this._last = this.InFullscreen();
        this._sig = global.display.connect('in-fullscreen-changed', () => {
            const now = this.InFullscreen();
            if (now !== this._last) {
                this._last = now;
                this._dbus.emit_signal('FullscreenChanged', new GLib.Variant('(b)', [now]));
            }
        });
    }

    InFullscreen() {
        const n = global.display.get_n_monitors();
        for (let i = 0; i < n; i++) {
            if (global.display.get_monitor_in_fullscreen(i))
                return true;
        }
        return false;
    }

    disable() {
        if (this._sig) {
            global.display.disconnect(this._sig);
            this._sig = null;
        }
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = 0;
        }
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
    }
}
