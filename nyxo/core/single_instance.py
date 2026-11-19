"""Single-instance guard (flock) + local-socket IPC so a second launch can
forward a command (show / chat / listen / quit) to the running instance."""
from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import paths

log = logging.getLogger("nyxo.instance")


def socket_name() -> str:
    return str(paths.runtime_dir() / "nyxo.sock")


def lock_path() -> Path:
    return paths.runtime_dir() / "nyxo.lock"


class InstanceLock:
    """Process-wide exclusive lock; released automatically if the process dies."""

    def __init__(self, path: Path | None = None):
        self.path = path or lock_path()
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


def send_command(cmd: str, timeout_ms: int = 1500, retry_s: float = 0.0) -> bool:
    """Send a command to the running instance. Returns True on delivery.
    `retry_s` covers the moment right after start-up when the instance holds
    the lock but its socket isn't listening yet."""
    import time
    deadline = time.monotonic() + retry_s
    while True:
        sock = QLocalSocket()
        sock.connectToServer(socket_name())
        if sock.waitForConnected(timeout_ms):
            sock.write((cmd + "\n").encode())
            sock.flush()
            sock.waitForBytesWritten(timeout_ms)
            sock.disconnectFromServer()
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


class CommandServer(QObject):
    command = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        QLocalServer.removeServer(socket_name())   # stale socket from a crash
        if not self.server.listen(socket_name()):
            log.error("IPC listen failed: %s", self.server.errorString())
        self.server.newConnection.connect(self._on_conn)

    def _on_conn(self) -> None:
        while self.server.hasPendingConnections():
            s = self.server.nextPendingConnection()
            s.readyRead.connect(lambda s=s: self._read(s))
            s.disconnected.connect(s.deleteLater)

    def _read(self, s: QLocalSocket) -> None:
        data = bytes(s.readAll()).decode(errors="ignore")
        for line in data.splitlines():
            line = line.strip()
            if line:
                log.info("IPC command: %s", line)
                self.command.emit(line)

    def close(self) -> None:
        self.server.close()
        QLocalServer.removeServer(socket_name())
