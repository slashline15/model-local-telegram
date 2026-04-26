from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED: bool = False

_RESET = "\x1b[0m"
_COLORS: dict[str, str] = {
    "DEBUG":    "\x1b[36m",   # ciano
    "INFO":     "\x1b[37m",   # branco
    "WARNING":  "\x1b[33m",   # amarelo
    "ERROR":    "\x1b[31m",   # vermelho
    "CRITICAL": "\x1b[1;41m", # vermelho fundo
}


class _ColorFormatter(logging.Formatter):
    """Formato compacto e colorido quando saída é TTY."""

    _USE_COLOR: bool = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    def format(self, record: logging.LogRecord) -> str:
        record.short_name = record.name.replace("__main__", "main")
        text = super().format(record)
        if not self._USE_COLOR:
            return text
        color = _COLORS.get(record.levelname, "")
        return f"{color}{text}{_RESET}" if color else text


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Configura o logger raiz uma única vez (idempotente)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(level.upper())

    console_fmt = _ColorFormatter(
        fmt="%(asctime)s.%(msecs)03d │ %(levelname)-7s │ %(short_name)-22s │ %(message)s",
        datefmt="%H:%M:%S",
    )
    file_fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(console_fmt)
    root.addHandler(stream_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(file_fmt)
        root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.INFO)
    logging.getLogger("telegram.bot").setLevel(logging.INFO)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
