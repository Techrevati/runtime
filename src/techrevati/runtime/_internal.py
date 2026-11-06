"""
Internal shared validators — single source of truth for the small
``_validate_*`` / ``_ensure_*`` helpers that were copy-pasted across modules.

Private (not re-exported from the package). Dependency-free on purpose: it imports
only the stdlib so any runtime module can import it without risking an import
cycle. Helpers that need a runtime type (``_validate_event`` → ``AgentEvent``,
``_validate_usage`` → ``UsageSnapshot``) intentionally stay local to their
modules to keep this module type-free.

Behavior — including every exception type and message string — is byte-identical
to the originals it replaces; the test suite pins those messages.
"""

from __future__ import annotations

import math
import sqlite3
import time
from contextlib import suppress
from pathlib import Path

_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS techrevati_runtime_metadata (
    component      TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL
);
"""

# Switching a fresh database to WAL needs an exclusive lock and SQLite does not
# always run the busy handler for it, so concurrent first opens fail immediately
# with "database is locked" unless retried here.
_WAL_RETRY_SECONDS = 5.0
_WAL_RETRY_INTERVAL_SECONDS = 0.05


def _validate_bool(field_name: str, value: bool) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a bool")
    return value


def _validate_non_empty_str(field_name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _validate_optional_non_empty_str(field_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _validate_non_empty_str(field_name, value)


def _validate_optional_str(field_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    return value


def _validate_positive_int(field_name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _validate_non_negative_int(field_name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _validate_project_id(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("project_id must be an integer or None")
    if value < 0:
        raise ValueError("project_id must be non-negative")
    return value


def _validate_finite_number(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def _validate_cost_usd(cost_usd: float) -> float:
    if isinstance(cost_usd, bool) or not isinstance(cost_usd, (int, float)):
        raise TypeError("cost_usd must be a number")
    cost = float(cost_usd)
    if not math.isfinite(cost):
        raise ValueError("cost_usd must be finite")
    if cost < 0:
        raise ValueError("cost_usd must be non-negative")
    return cost


def _validate_model(model: str) -> str:
    if not isinstance(model, str):
        raise TypeError("model must be a string")
    if not model.strip():
        raise ValueError("model must not be empty")
    return model.strip()


def _is_sqlite_lock_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _open_wal(path: str | Path) -> sqlite3.Connection:
    """Open a sqlite connection in WAL mode before any schema mutations.

    WAL is a no-op for ``:memory:``; requesting it there is harmless. Lock
    contention from concurrent first opens is retried for a bounded time; any
    other failure closes the connection and propagates.
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    deadline = time.monotonic() + _WAL_RETRY_SECONDS
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            if _is_sqlite_lock_error(exc) and time.monotonic() < deadline:
                time.sleep(_WAL_RETRY_INTERVAL_SECONDS)
                continue
            with suppress(Exception):
                conn.close()
            raise
        except Exception:
            with suppress(Exception):
                conn.close()
            raise
        return conn


def _ensure_schema_version(
    conn: sqlite3.Connection, *, component: str, version: int
) -> None:
    """Record ``component``'s schema version, or reject an unsupported one.

    Safe under concurrent first opens: the insert ignores a row another process
    committed first, and the version is compared against whichever row won.
    """
    conn.executescript(_METADATA_SCHEMA)
    select = (
        "SELECT schema_version FROM techrevati_runtime_metadata WHERE component = ?"
    )
    row = conn.execute(select, (component,)).fetchone()
    if row is None:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO techrevati_runtime_metadata"
                " (component, schema_version) VALUES (?, ?)",
                (component, version),
            )
        row = conn.execute(select, (component,)).fetchone()
    observed = int(row[0])
    if observed != version:
        raise RuntimeError(
            f"unsupported sqlite schema for {component}: "
            f"version {observed}, expected {version}"
        )
