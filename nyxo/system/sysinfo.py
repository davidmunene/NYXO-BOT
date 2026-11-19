"""System information via psutil (read-only)."""
from __future__ import annotations

import socket
import time

import psutil


def _gb(n: float) -> str:
    return f"{n / 1024**3:.1f} GB"


def cpu() -> str:
    pct = psutil.cpu_percent(interval=0.4)
    freq = psutil.cpu_freq()
    load = psutil.getloadavg()
    f = f" @ {freq.current / 1000:.2f} GHz" if freq else ""
    return (f"CPU: {pct:.0f}% busy across {psutil.cpu_count()} threads{f}. "
            f"Load average {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}.")


def ram() -> str:
    m = psutil.virtual_memory()
    s = psutil.swap_memory()
    return (f"RAM: {_gb(m.used)} used of {_gb(m.total)} ({m.percent:.0f}%), "
            f"{_gb(m.available)} available. Swap: {_gb(s.used)} of {_gb(s.total)}.")


def disk() -> str:
    lines = []
    seen = set()
    for part in psutil.disk_partitions(all=False):
        if part.fstype in ("squashfs", "tmpfs", "overlay") or part.mountpoint.startswith("/snap"):
            continue
        if part.device in seen:
            continue
        seen.add(part.device)
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        lines.append(f"{part.mountpoint}: {_gb(u.used)} used of {_gb(u.total)} "
                     f"({u.percent:.0f}%), {_gb(u.free)} free")
    return "Disk:\n" + "\n".join(lines) if lines else "Disk: no mounted drives found."


def battery() -> str:
    b = psutil.sensors_battery()
    if b is None:
        return "Battery: no battery detected."
    state = "charging" if b.power_plugged else "on battery"
    left = ""
    if not b.power_plugged and b.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
        h, m = divmod(int(b.secsleft) // 60, 60)
        left = f", about {h}h {m:02d}m left"
    return f"Battery: {b.percent:.0f}% ({state}{left})."


def online(timeout: float = 2.0) -> bool:
    for host in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            with socket.create_connection(host, timeout=timeout):
                return True
        except OSError:
            continue
    return False


def network() -> str:
    ups = [n for n, s in psutil.net_if_stats().items() if s.isup and n != "lo"]
    addrs = psutil.net_if_addrs()
    parts = []
    for n in ups:
        ips = [a.address for a in addrs.get(n, []) if a.family == socket.AF_INET]
        if ips:
            parts.append(f"{n} ({ips[0]})")
    net = "connected to the internet" if online() else "NOT reaching the internet"
    return f"Network: {net}. Active interfaces: {', '.join(parts) or 'none'}."


def uptime() -> str:
    secs = int(time.time() - psutil.boot_time())
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"Uptime: {d}d {h}h {m}m." if d else f"Uptime: {h}h {m}m."


KINDS = {"cpu": cpu, "ram": ram, "memory": ram, "disk": disk, "storage": disk,
         "battery": battery, "network": network, "uptime": uptime}


def summary() -> str:
    return "\n".join(fn() for fn in (cpu, ram, disk, battery, network, uptime))


def snapshot() -> dict:
    """Numbers for the AI context / tests."""
    m = psutil.virtual_memory()
    b = psutil.sensors_battery()
    return {"cpu_percent": psutil.cpu_percent(interval=None), "ram_percent": m.percent,
            "battery": b.percent if b else None}
