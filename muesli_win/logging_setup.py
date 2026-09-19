"""Rotating file log plus console. The log is the first thing to ask for when
a hotkey mysteriously stops working, so it is on by default and capped."""
from __future__ import annotations

import logging
import logging.handlers
import sys

from . import paths

FMT = "%(asctime)s %(levelname)-7s %(name)-28s %(message)s"


def setup(level: str = "INFO") -> None:
    paths.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)

    fh = logging.handlers.RotatingFileHandler(
        paths.logs_dir() / "muesli.log", maxBytes=2_000_000, backupCount=3,
        encoding="utf-8")
    fh.setFormatter(logging.Formatter(FMT))
    root.addHandler(fh)

    # A windowed PyInstaller build has no stdout; writing to it raises.
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter(FMT))
        root.addHandler(sh)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def log_path() -> str:
    return str(paths.logs_dir() / "muesli.log")
