from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.log_context import get_run_id

_CONFIGURED: bool = False


def _should_use_color() -> bool:
    """Determina se cores devem ser usadas na saída."""
    if os.environ.get("NO_COLOR") not in (None, ""):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
        return True
    term = os.environ.get("TERM", "").lower()
    color_terms = {"xterm", "xterm-256color", "screen", "tmux", "linux", "rxvt"}
    if term in color_terms:
        return True
    if os.environ.get("TERM_PROGRAM") == "vscode":
        return True
    return False


class _RunIdFilter(logging.Filter):
    """Injeta o run_id corrente no record para uso no formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = get_run_id()
        record.short_name = record.name.replace("__main__", "main")
        return True


def _ensure_utf8_stdout() -> None:
    """Console do Windows é cp1252 por padrão — Unicode (│, →, emojis) quebra.

    Reconfigura stdout/stderr para utf-8 quando possível. Idempotente.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _build_console_handler(use_color: bool) -> logging.Handler:
    if use_color:
        try:
            from rich.console import Console
            from rich.logging import RichHandler

            console = Console(
                file=sys.stdout,
                force_terminal=True,
                soft_wrap=False,
            )
            handler: logging.Handler = RichHandler(
                console=console,
                show_time=True,
                show_level=True,
                show_path=False,
                rich_tracebacks=True,
                markup=False,
                log_time_format="%H:%M:%S",
                omit_repeated_times=False,
            )
            handler.setFormatter(
                logging.Formatter(fmt="%(run_id)s | %(short_name)-22s | %(message)s")
            )
            return handler
        except ImportError:
            pass

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s.%(msecs)03d | %(levelname)-7s | %(run_id)s | "
            "%(short_name)-22s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    return handler


def setup_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    use_color: bool | None = None,
) -> None:
    """Configura o logger raiz uma única vez (idempotente)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    _ensure_utf8_stdout()
    color = use_color if use_color is not None else _should_use_color()

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.addFilter(_RunIdFilter())

    console_handler = _build_console_handler(color)
    console_handler.addFilter(_RunIdFilter())
    root.addHandler(console_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(run_id)s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        file_handler.addFilter(_RunIdFilter())
        root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.INFO)
    logging.getLogger("telegram.bot").setLevel(logging.INFO)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
