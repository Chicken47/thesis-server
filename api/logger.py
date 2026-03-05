"""
Centralised logging configuration.

Usage in any module:
    from api.logger import get_logger
    log = get_logger(__name__)

    log.info("Job started", extra={"job_id": job_id, "ticker": ticker})
    log.error("Scrape failed", exc_info=True)

Log levels (set LOG_LEVEL env var):
    DEBUG   — verbose, includes RAG chunks / prompt sizes
    INFO    — default, one line per meaningful event
    WARNING — something unexpected but recoverable
    ERROR   — exception / job failed

Format:
    2025-01-01 12:00:00 | INFO  | api.jobs | [TCS] job=abc-123 step=scrape msg=started
"""

import logging
import os
import sys

# ── Safe makeRecord patch ──────────────────────────────────────────────────────
# Python's Logger.makeRecord raises KeyError if extra= contains a key that
# matches a built-in LogRecord attribute (e.g. "name", "message", "pathname").
# This patch silently drops those keys instead of crashing the caller.
_RESERVED_KEYS = (
    frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
    | {"message", "asctime"}
)
_orig_make_record = logging.Logger.makeRecord


def _safe_make_record(self, name, level, fn, lno, msg, args, exc_info,
                      func=None, extra=None, sinfo=None):
    if extra:
        extra = {k: v for k, v in extra.items() if k not in _RESERVED_KEYS}
    return _orig_make_record(self, name, level, fn, lno, msg, args, exc_info,
                             func, extra, sinfo)


logging.Logger.makeRecord = _safe_make_record  # type: ignore[method-assign]
# ──────────────────────────────────────────────────────────────────────────────


class _PacFormatter(logging.Formatter):
    """
    Coloured single-line formatter.

    Colours map to severity the same way Pac-Man ghosts do:
      DEBUG   → cyan  (Inky)
      INFO    → white
      WARNING → yellow (Pac-Man itself)
      ERROR   → red   (Blinky)
      CRITICAL→ pink  (Pinky)
    """

    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    CYAN   = "\033[36m"
    WHITE  = "\033[37m"
    YELLOW = "\033[33m"
    RED    = "\033[31m"
    PINK   = "\033[35m"
    DIM    = "\033[2m"

    LEVEL_STYLES = {
        logging.DEBUG:    CYAN,
        logging.INFO:     WHITE,
        logging.WARNING:  YELLOW,
        logging.ERROR:    RED,
        logging.CRITICAL: PINK + BOLD,
    }

    # Reserved LogRecord attributes — never treat these as user extras
    _RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        colour = self.LEVEL_STYLES.get(record.levelno, self.WHITE)
        level  = f"{colour}{record.levelname:<8}{self.RESET}"
        logger_name = f"{self.DIM}{record.name}{self.RESET}"
        ts     = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        msg    = record.getMessage()

        # Append any extra key=value pairs (job_id, ticker, step, etc.)
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in self._RESERVED and k not in ("message", "asctime")
        }
        if extras:
            kv = "  " + "  ".join(f"{self.DIM}{k}{self.RESET}={v}" for k, v in extras.items())
        else:
            kv = ""

        line = f"{self.DIM}{ts}{self.RESET} | {level} | {logger_name} | {msg}{kv}"

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)

        return line


def _configure_root() -> None:
    """One-time root logger setup. Called on first import."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    if root.handlers:
        return  # already configured (e.g. gunicorn already set up)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_PacFormatter())
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet noisy third-party libs
    for noisy in ("httpx", "httpcore", "hpack", "urllib3",
                   "chromadb.telemetry", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)


_configure_root()


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Use __name__ as the name."""
    return logging.getLogger(name)
