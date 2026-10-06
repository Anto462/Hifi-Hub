"""
Playlists internas (en SQLite) e import/export M3U8.

M3U8 es el formato de playlist interoperable (Foobar2000, VLC, etc.). Se exporta
con la cabecera extendida (#EXTINF) para conservar duración y "Artista - Título".
"""

from __future__ import annotations

import time
from pathlib import Path

from hifihub.library.db import Library


class Playlists:
    def __init__(self, lib: Library) -> None:
        self.lib = lib
        self.conn = lib.conn

    def create(self, name: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO playlists (name, created_at) VALUES (?, ?)", (name, time.time())
        )
        self.conn.commit()
        return cur.lastrowid

    def delete(self, playlist_id: int) -> None:
        self.conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        self.conn.commit()

    def rename(self, playlist_id: int, name: str) -> None:
        self.conn.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, playlist_id))
        self.conn.commit()

    def list_all(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT p.id, p.name, COUNT(pt.track_id) AS n
               FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
               GROUP BY p.id ORDER BY p.name"""
        ).fetchall()
        return [dict(r) for r in rows]

    def add_track(self, playlist_id: int, track_id: int) -> None:
        pos = self.conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()["p"]
        self.conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
            (playlist_id, track_id, pos),
        )
        self.conn.commit()

    def add_tracks(self, playlist_id: int, track_ids: list[int]) -> dict:
        """Añade varias pistas de golpe (un álbum, un artista, una mezcla).

        Usa `add_track` hace un commit por pista, así que
        añadir un álbum de 20 eran 20 commits. Omite las que ya están en esa
        playlist para no duplicar, y devuelve el recuento para poder informar.
        """
        existing = {
            r["track_id"]
            for r in self.conn.execute(
                "SELECT track_id FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)
            )
        }
        pos = self.conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()["p"]

        # Validar los ids contra la tabla: un id inexistente dispara un fallo de
        # clave foránea que abortaría TODO el lote (basta una pista borrada entre
        # medias para tumbar el álbum entero).
        wanted = []
        for tid in track_ids or []:
            try:
                wanted.append(int(tid))
            except (TypeError, ValueError):
                continue
        valid: set[int] = set()
        if wanted:
            marks = ",".join("?" * len(wanted))
            valid = {
                r["id"]
                for r in self.conn.execute(
                    f"SELECT id FROM tracks WHERE id IN ({marks})", wanted
                )
            }

        added = skipped = missing = 0
        rows = []
        for tid in wanted:
            if tid not in valid:
                missing += 1
                continue
            if tid in existing:
                skipped += 1
                continue
            existing.add(tid)          # evita duplicados dentro del propio lote
            rows.append((playlist_id, tid, pos))
            pos += 1
            added += 1
        if rows:
            self.conn.executemany(
                "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
                rows,
            )
            self.conn.commit()
        return {"added": added, "skipped": skipped, "missing": missing}

    def remove_track(self, playlist_id: int, track_id: int) -> None:
        self.conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        )
        self.conn.commit()

    def move(self, playlist_id: int, position: int, delta: int) -> bool:
        """Mueve la entrada en `position` (índice 0-based del orden actual)
        arriba/abajo. Por rowid: seguro aunque una pista esté repetida."""
        rows = self.conn.execute(
            """SELECT rowid, position FROM playlist_tracks
               WHERE playlist_id = ? ORDER BY position""",
            (playlist_id,),
        ).fetchall()
        target = position + delta
        if not (0 <= position < len(rows)) or not (0 <= target < len(rows)):
            return False
        a, b = rows[position], rows[target]
        self.conn.execute("UPDATE playlist_tracks SET position = ? WHERE rowid = ?",
                          (b["position"], a["rowid"]))
        self.conn.execute("UPDATE playlist_tracks SET position = ? WHERE rowid = ?",
                          (a["position"], b["rowid"]))
        self.conn.commit()
        return True

    def tracks(self, playlist_id: int) -> list:
        return self.conn.execute(
            """SELECT t.* FROM playlist_tracks pt
               JOIN tracks t ON t.id = pt.track_id
               WHERE pt.playlist_id = ? ORDER BY pt.position""",
            (playlist_id,),
        ).fetchall()

    # --- M3U8 -----------------------------------------------------------

    def export_m3u8(self, playlist_id: int, out_path: Path | str) -> Path:
        out_path = Path(out_path)
        lines = ["#EXTM3U"]
        for t in self.tracks(playlist_id):
            dur = int(t["duration"] or 0)
            artist = t["artist"] or ""
            title = t["title"] or Path(t["path"]).stem
            lines.append(f"#EXTINF:{dur},{artist} - {title}")
            lines.append(t["path"])
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out_path

    def import_m3u8(self, m3u_path: Path | str, name: str | None = None) -> int:
        m3u_path = Path(m3u_path)
        name = name or m3u_path.stem
        playlist_id = self.create(name)
        for raw in m3u_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            track = self.lib.get_by_path(_resolve(line, m3u_path))
            if track:
                self.add_track(playlist_id, track["id"])
        return playlist_id


def _resolve(entry: str, m3u_path: Path) -> str:
    p = Path(entry)
    if not p.is_absolute():
        p = (m3u_path.parent / p).resolve()
    return str(p)
