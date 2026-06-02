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

_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS techrevati_runtime_metadata (
    component      TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL
);
"""


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


def _ensure_schema_version(
    conn: sqlite3.Connection, *, component: str, version: int
) -> None:
    conn.executescript(_METADATA_SCHEMA)
    row = conn.execute(
        "SELECT schema_version FROM techrevati_runtime_metadata WHERE component = ?",
        (component,),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO techrevati_runtime_metadata"
            " (component, schema_version) VALUES (?, ?)",
            (component, version),
        )
        return
    observed = int(row[0])
    if observed != version:
        raise RuntimeError(
            f"unsupported sqlite schema for {component}: "
            f"version {observed}, expected {version}"
        )
