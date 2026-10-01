"""SQLite persistence for scan history and dashboard statistics."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "database" / "scans.db"


def get_connection(database_path: str | Path = DEFAULT_DATABASE_PATH) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def _managed_connection(database_path: str | Path) -> Iterator[sqlite3.Connection]:
    connection = get_connection(database_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
    with _managed_connection(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                prediction TEXT NOT NULL CHECK (prediction IN ('Legitimate', 'Suspicious', 'Phishing')),
                risk_score REAL NOT NULL CHECK (risk_score >= 0.0 AND risk_score <= 100.0),
                risk_level TEXT NOT NULL CHECK (risk_level IN ('Low', 'Medium', 'High')),
                scanned_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                source TEXT NOT NULL DEFAULT 'Manual',
                reasons TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        # Safely migrate existing databases if new columns are missing
        existing_cols = {
            row[1] for row in connection.execute("PRAGMA table_info(scan_history)").fetchall()
        }
        if "source" not in existing_cols:
            connection.execute("ALTER TABLE scan_history ADD COLUMN source TEXT NOT NULL DEFAULT 'Manual'")
        if "reasons" not in existing_cols:
            connection.execute("ALTER TABLE scan_history ADD COLUMN reasons TEXT NOT NULL DEFAULT '[]'")


def add_scan(
    url: str,
    prediction: str,
    risk_score: float | int,
    risk_level: str,
    source_or_database_path: str | Path | None = None,
    reasons: list[str] | str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    source: str = "Manual",
) -> int:
    """Save a scan result with backwards compatibility for legacy positional arguments."""
    actual_db_path = database_path
    actual_source = source

    # Handle legacy 5th positional argument (database_path)
    if source_or_database_path is not None:
        if isinstance(source_or_database_path, Path) or (
            isinstance(source_or_database_path, str)
            and (source_or_database_path.endswith(".db") or "/" in source_or_database_path or "\\" in source_or_database_path)
        ):
            actual_db_path = source_or_database_path
        else:
            actual_source = str(source_or_database_path)

    if isinstance(reasons, list):
        reasons_json = json.dumps(reasons, ensure_ascii=False)
    elif isinstance(reasons, str):
        reasons_json = reasons
    else:
        reasons_json = "[]"

    float_score = round(float(risk_score), 1)

    with _managed_connection(actual_db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO scan_history (url, prediction, risk_score, risk_level, source, reasons)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (url, prediction, float_score, risk_level, actual_source, reasons_json),
        )
        return int(cursor.lastrowid)


def get_history(database_path: str | Path = DEFAULT_DATABASE_PATH, limit: int = 100) -> list[dict[str, Any]]:
    initialize_database(database_path)
    with _managed_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, url, prediction, risk_score, risk_level, scanned_at, source, reasons
            FROM scan_history ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()

    history: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["risk_score"] = round(float(item["risk_score"]), 1)
        raw_reasons = item.get("reasons") or "[]"
        try:
            item["reasons"] = json.loads(raw_reasons) if isinstance(raw_reasons, str) else raw_reasons
        except Exception:
            item["reasons"] = [raw_reasons] if raw_reasons else []
        history.append(item)
    return history


def get_dashboard_stats(database_path: str | Path = DEFAULT_DATABASE_PATH) -> dict[str, Any]:
    initialize_database(database_path)
    with _managed_connection(database_path) as connection:
        totals = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN risk_level = 'Low' OR prediction = 'Legitimate' THEN 1 ELSE 0 END) AS legitimate,
                SUM(CASE WHEN risk_level = 'Medium' OR prediction = 'Suspicious' THEN 1 ELSE 0 END) AS suspicious,
                SUM(CASE WHEN risk_level = 'High' OR prediction = 'Phishing' THEN 1 ELSE 0 END) AS phishing,
                SUM(CASE WHEN source LIKE 'QR%' THEN 1 ELSE 0 END) AS qr_scans,
                AVG(risk_score) AS avg_risk_score
            FROM scan_history
            """
        ).fetchone()

        recent = connection.execute(
            """
            SELECT id, url, prediction, risk_score, risk_level, scanned_at, source, reasons
            FROM scan_history ORDER BY id DESC LIMIT 5
            """
        ).fetchall()

    recent_list: list[dict[str, Any]] = []
    for row in recent:
        item = dict(row)
        item["risk_score"] = round(float(item["risk_score"]), 1)
        raw_reasons = item.get("reasons") or "[]"
        try:
            item["reasons"] = json.loads(raw_reasons) if isinstance(raw_reasons, str) else raw_reasons
        except Exception:
            item["reasons"] = [raw_reasons] if raw_reasons else []
        recent_list.append(item)

    total_count = totals["total"] or 0
    qr_count = totals["qr_scans"] or 0
    avg_score = round(float(totals["avg_risk_score"]), 1) if totals["avg_risk_score"] is not None else 0.0

    return {
        "total_scans": total_count,
        "legitimate_count": totals["legitimate"] or 0,
        "suspicious_count": totals["suspicious"] or 0,
        "phishing_count": totals["phishing"] or 0,
        "high_risk_count": totals["phishing"] or 0,
        "qr_scans_count": qr_count,
        "manual_scans_count": max(0, total_count - qr_count),
        "average_risk_score": avg_score,
        "recent_scans": recent_list,
    }


def clear_history(database_path: str | Path = DEFAULT_DATABASE_PATH) -> int:
    initialize_database(database_path)
    with _managed_connection(database_path) as connection:
        cursor = connection.execute("DELETE FROM scan_history")
        return cursor.rowcount

