"""Registro histórico de descargas (SQLite propio, CONFIG_DIR/registry.db).

Aunque inicialmente era netamente basado en las descargas para conocer bajo qué URL, con qué perfil, cuándo, y si terminó bien una descarga en la actualidad se usa mas que nada a lo interno. De igual manera dejo el code para montar la tabla de sqlite y la clase y funciones que la consumen.

De igual manera algo de registro supongo no esta mal y sirve para evitar duplicar descargas.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from hifihub import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id          INTEGER PRIMARY KEY,
    url         TEXT NOT NULL,
    status      TEXT NOT NULL,          -- ok | error
    error       TEXT,
    profile     TEXT,
    template    TEXT,
    dest        TEXT,
    attempts    INTEGER DEFAULT 1,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_downloads_url ON downloads(url);
"""


class Registry:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (paths.CONFIG_DIR / "registry.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def record(
        self,
        url: str,
        status: str,
        *,
        error: str | None = None,
        profile: str = "",
        template: str = "",
        dest: str = "",
        attempts: int = 1,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO downloads (url, status, error, profile, template, dest, attempts, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (url, status, error, profile, template, dest, attempts, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def recent(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM downloads ORDER BY finished_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()
