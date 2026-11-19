"""Logging: rotating file in ~/.local/state/nyxo/logs plus stderr (-> journald)."""
from __future__ import annotations

import logging
import logging.handlers
import sys

from . import paths

_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup(verbose: bool = False) -> logging.Logger:
    paths.log_dir().mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if getattr(root, "_nyxo_configured", False):
        return logging.getLogger("nyxo")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(
        paths.log_dir() / "nyxo.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FMT))
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(sh)
    root._nyxo_configured = True  # type: ignore[attr-defined]
    return logging.getLogger("nyxo")
