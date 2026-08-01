"""SQLite run history for the tracking dashboard."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from src.config import PROJECT_ROOT, get_env

DB_PATH = Path(get_env("DB_PATH", str(PROJECT_ROOT / "runs.db")))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                country_code TEXT NOT NULL,
                country_name TEXT NOT NULL,
                run_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                started_at TEXT,
                finished_at TEXT,
                trends_json TEXT,
                news_json TEXT,
                script_path TEXT,
                video_path TEXT,
                youtube_video_id TEXT,
                error_message TEXT,
                steps_log TEXT
            )
            """
        )
        conn.commit()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def create_run(country_code: str, country_name: str, run_date: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO runs (country_code, country_name, run_date, status, started_at, steps_log)
            VALUES (?, ?, ?, 'running', ?, '[]')
            """,
            (country_code.upper(), country_name, run_date, now),
        )
        return int(cur.lastrowid)


def update_run(run_id: int, **fields: Any) -> None:
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [run_id]
    with db() as conn:
        conn.execute(f"UPDATE runs SET {columns} WHERE id = ?", values)


def append_step_log(run_id: int, step: str, detail: str = "") -> None:
    with db() as conn:
        row = conn.execute("SELECT steps_log FROM runs WHERE id = ?", (run_id,)).fetchone()
        log: list[dict[str, str]] = json.loads(row["steps_log"] or "[]")
        log.append(
            {
                "step": step,
                "detail": detail,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        conn.execute(
            "UPDATE runs SET steps_log = ? WHERE id = ?",
            (json.dumps(log), run_id),
        )


def finish_run(run_id: int, status: str, error_message: str | None = None) -> None:
    update_run(
        run_id,
        status=status,
        finished_at=datetime.now(timezone.utc).isoformat(),
        error_message=error_message,
    )


def get_run(run_id: int) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_runs(
    country_code: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM runs"
    params: list[Any] = []
    if country_code:
        query += " WHERE country_code = ?"
        params.append(country_code.upper())
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with db() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def count_runs_today() -> dict[str, int]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE run_date = ?", (today,)
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE run_date = ? AND status = 'failed'", (today,)
        ).fetchone()[0]
        success = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE run_date = ? AND status = 'success'", (today,)
        ).fetchone()[0]
        running = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status = 'running'", ()
        ).fetchone()[0]
    return {
        "today_total": total,
        "today_success": success,
        "today_failed": failed,
        "running": running,
    }
