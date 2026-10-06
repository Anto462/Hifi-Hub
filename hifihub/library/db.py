"""
Biblioteca musical usa SQLite.

Una tabla `tracks` denormalizada (artista/álbum como texto) porque la colección
proviene de descargas sueltas, no de un catálogo relacional estricto. Los álbumes
y artistas se derivan por consulta. Búsqueda instantánea con FTS5 (fallback a LIKE
si el build de SQLite no lo trae).
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from hifihub import paths

# Separadores de colaboración para extraer el artista PRINCIPAL. Se incluyen los
# que la biblioteca usa de verdad (coma, barra) y los inequívocos (feat/ft). Se
# dejan fuera separadores ambiguos (" x ", " vs ", " with ") que romperían nombres
# como "Malcolm X" con más facilidad; los nombres compuestos legítimos con coma/&
# ("Simon & Garfunkel", "Tyler, the Creator") se protegen aparte con la regla de
# "2+ pistas" del plegado, no aquí.
_COLLAB_SEP = re.compile(
    r"\s*(?:,|;|/|&|\+|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b)\s*", re.IGNORECASE
)


def main_artist(name: str | None) -> str | None:
    """Artista principal de una cadena de colaboración (el primero).

    "50 Cent, Akon" -> "50 Cent"; "Shihoko Hirata / Lotus Juice" -> "Shihoko
    Hirata". Devuelve el nombre tal cual si no hay separador o queda vacío.
    """
    if not name:
        return name
    first = _COLLAB_SEP.split(name, maxsplit=1)[0].strip()
    return first or name

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id            INTEGER PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,
    title         TEXT,
    artist        TEXT,
    album         TEXT,
    album_artist  TEXT,
    genre         TEXT,
    date          TEXT,
    track_number  INTEGER,
    duration      REAL,
    codec         TEXT,
    sample_rate   INTEGER,
    bit_depth     INTEGER,
    bit_rate      INTEGER,
    lossless      INTEGER DEFAULT 0,
    cover_path    TEXT,
    mtime         REAL,
    added_at      REAL,
    play_count    INTEGER DEFAULT 0,
    last_played   REAL
);
CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album, album_artist);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);

CREATE TABLE IF NOT EXISTS playlists (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  REAL
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
    title, artist, album, content='tracks', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS tracks_ai AFTER INSERT ON tracks BEGIN
    INSERT INTO tracks_fts(rowid, title, artist, album)
    VALUES (new.id, new.title, new.artist, new.album);
END;
CREATE TRIGGER IF NOT EXISTS tracks_ad AFTER DELETE ON tracks BEGIN
    INSERT INTO tracks_fts(tracks_fts, rowid, title, artist, album)
    VALUES ('delete', old.id, old.title, old.artist, old.album);
END;
CREATE TRIGGER IF NOT EXISTS tracks_au AFTER UPDATE ON tracks BEGIN
    INSERT INTO tracks_fts(tracks_fts, rowid, title, artist, album)
    VALUES ('delete', old.id, old.title, old.artist, old.album);
    INSERT INTO tracks_fts(rowid, title, artist, album)
    VALUES (new.id, new.title, new.artist, new.album);
END;
"""


@dataclass
class Track:
    path: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    genre: str | None = None
    date: str | None = None
    track_number: int | None = None
    duration: float | None = None
    codec: str | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    bit_rate: int | None = None
    lossless: bool = False
    cover_path: str | None = None
    mtime: float | None = None


class Library:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (paths.CONFIG_DIR / "library.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.has_fts = self._init_schema()

    def _init_schema(self) -> bool:
        self.conn.executescript(SCHEMA)
        try:
            self.conn.executescript(FTS_SCHEMA)
            self.conn.commit()
            return True
        except sqlite3.OperationalError:
            self.conn.commit()
            return False

    def close(self) -> None:
        self.conn.close()

    # --- Tracks ---------------------------------------------------------

    def upsert_track(self, track: Track) -> int:
        data = asdict(track)
        data["lossless"] = 1 if track.lossless else 0
        existing = self.conn.execute(
            "SELECT id FROM tracks WHERE path = ?", (track.path,)
        ).fetchone()
        cols = [k for k in data if k != "path"]
        if existing:
            assignments = ", ".join(f"{c} = :{c}" for c in cols)
            self.conn.execute(
                f"UPDATE tracks SET {assignments} WHERE path = :path", data
            )
            track_id = existing["id"]
        else:
            data["added_at"] = time.time()
            allcols = list(data.keys())
            placeholders = ", ".join(f":{c}" for c in allcols)
            cur = self.conn.execute(
                f"INSERT INTO tracks ({', '.join(allcols)}) VALUES ({placeholders})", data
            )
            track_id = cur.lastrowid
        self.conn.commit()
        return track_id

    def get_track(self, track_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    def get_by_path(self, path: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()

    def all_paths_mtime(self) -> dict[str, float]:
        """Para escaneo incremental: {path: mtime} de lo ya indexado."""
        rows = self.conn.execute("SELECT path, mtime FROM tracks").fetchall()
        return {r["path"]: r["mtime"] for r in rows}

    def delete_track(self, track_id: int) -> None:
        """Quita una pista de la BIBLIOTECA (la fila). El archivo no se toca."""
        self.conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        self.conn.commit()

    def delete_missing(self, existing_paths: set[str]) -> int:
        """Borra de la BD las pistas cuyo archivo ya no está en disco."""
        db_paths = {r["path"] for r in self.conn.execute("SELECT path FROM tracks")}
        gone = db_paths - existing_paths
        for p in gone:
            self.conn.execute("DELETE FROM tracks WHERE path = ?", (p,))
        self.conn.commit()
        return len(gone)

    def list_tracks(self, album: str | None = None, artist: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM tracks"
        conds, args = [], []
        if album is not None:
            conds.append("album IS ?")
            args.append(album)
        if artist is not None:
            conds.append("(artist = ? OR album_artist = ?)")
            args += [artist, artist]
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY album, track_number, title"
        return self.conn.execute(q, args).fetchall()

    def list_albums(self, artist: str | None = None) -> list[sqlite3.Row]:
        # Un álbum se agrupa por su TÍTULO (no por título+artista), para que una
        # recopilación con varios artistas aparezca UNA sola vez. Cuando el álbum
        # reúne varios artistas se etiqueta "Varios artistas". Las pistas sin álbum
        # (album NULL o cadena vacía) colapsan todas en una única tarjeta
        # "(sin álbum)" gracias a NULLIF(album, '').
        q = """
            SELECT NULLIF(album, '') AS album,
                   CASE WHEN COUNT(DISTINCT COALESCE(album_artist, artist)) > 1
                        THEN 'Varios artistas'
                        ELSE MAX(COALESCE(album_artist, artist)) END AS album_artist,
                   COUNT(*)      AS track_count,
                   SUM(duration) AS total_duration,
                   MAX(cover_path) AS cover_path,
                   MIN(date)     AS date,
                   MIN(lossless) AS all_lossless,
                   MAX(added_at) AS added_at,
                   SUM(play_count) AS play_count
            FROM tracks
            """
        args: list = []
        if artist is not None:
            # Todas las cadenas brutas que se pliegan en este artista (incluye sus
            # colaboraciones), para que su página muestre esos álbumes también.
            raws = [r for r in self._raws_for_artist(artist) if r is not None]
            if raws:
                q += " WHERE COALESCE(album_artist, artist) IN (%s)" % ",".join("?" * len(raws))
                args += raws
            else:
                q += " WHERE album_artist IS NULL AND artist IS NULL"
        q += """
            GROUP BY NULLIF(album, '')
            ORDER BY album_artist COLLATE NOCASE, date, album COLLATE NOCASE
            """
        return self.conn.execute(q, args).fetchall()

    def album_tracks(self, album: str | None) -> list[sqlite3.Row]:
        """Pistas de un álbum para la vista de detalle / reproducir / borrar.

        album None o '' = bucket "sin álbum" (NULL y cadena vacía juntas). A
        diferencia de list_tracks(album=None), aquí None NO significa "todas".
        """
        if album is None or album == "":
            return self.conn.execute(
                "SELECT * FROM tracks WHERE album IS NULL OR album = '' "
                "ORDER BY COALESCE(album_artist, artist) COLLATE NOCASE, track_number, title"
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM tracks WHERE album = ? ORDER BY track_number, title",
            (album,),
        ).fetchall()

    def _artist_fold_map(self) -> dict[str | None, str | None]:
        """
        Una colaboración que aparece en 1 sola pista se
        pliega en su artista principal; si aparece en 2+ pistas se conserva tal
        cual (dúo/grupo recurrente). Los artistas sin separador se conservan.
        Así "50 Cent, Akon" (1 pista) cae bajo "50 Cent", pero "Simon & Garfunkel"
        (varias pistas) se mantiene intacto.
        """
        rows = self.conn.execute(
            "SELECT COALESCE(album_artist, artist) AS name, COUNT(*) AS n "
            "FROM tracks GROUP BY COALESCE(album_artist, artist)"
        ).fetchall()
        fold: dict[str | None, str | None] = {}
        for r in rows:
            name, n = r["name"], r["n"]
            mp = main_artist(name)
            fold[name] = mp if (name is not None and mp != name and n == 1) else name
        return fold

    def _raws_for_artist(self, name: str | None) -> list[str | None]:
        """
        Acepta tanto una clave mostrada ("50 Cent") como una cadena bruta que se
        plegó ("50 Cent, Akon"), para que el enlace desde una colaboración lleve a
        la página correcta en vez de a una vacía.
        """
        fold = self._artist_fold_map()
        values = set(fold.values())
        target = name if name in values else fold.get(name, name)
        raws = [raw for raw, key in fold.items() if key == target]
        return raws or [name]

    def list_artists(self) -> list[dict]:
        """Artistas agregados por artista PRINCIPAL (ver _artist_fold_map)."""
        fold = self._artist_fold_map()
        rows = self.conn.execute(
            "SELECT COALESCE(album_artist, artist) AS raw, album, duration, cover_path "
            "FROM tracks"
        ).fetchall()
        agg: dict[str | None, dict] = {}
        for r in rows:
            key = fold.get(r["raw"], r["raw"])
            a = agg.setdefault(key, {"albums": set(), "tracks": 0, "dur": 0.0, "cover": None})
            a["albums"].add(r["album"])
            a["tracks"] += 1
            a["dur"] += r["duration"] or 0
            if a["cover"] is None and r["cover_path"]:
                a["cover"] = r["cover_path"]
        out = [
            {"name": key, "album_count": len(a["albums"]), "track_count": a["tracks"],
             "total_duration": a["dur"], "cover_path": a["cover"]}
            for key, a in agg.items()
        ]
        out.sort(key=lambda d: (d["name"] or "").lower())
        return out

    def resolve_artist(self, name: str | None) -> str | None:
        """Clave de artista mostrada para `name` (si llega una colaboración plegada,
        devuelve su artista principal). Para títulos coherentes en su página."""
        fold = self._artist_fold_map()
        if name in set(fold.values()):
            return name
        return fold.get(name, name)

    def tracks_by_artist(self, name: str | None) -> list[sqlite3.Row]:
        """Pistas del artista `name` según el plegado (incluye sus colaboraciones)."""
        raws = self._raws_for_artist(name)
        return self._tracks_where_artist_in(raws)

    def _tracks_where_artist_in(self, raws: list[str | None]) -> list[sqlite3.Row]:
        non_null = [r for r in raws if r is not None]
        conds, args = [], []
        if non_null:
            conds.append("COALESCE(album_artist, artist) IN (%s)" % ",".join("?" * len(non_null)))
            args += non_null
        if any(r is None for r in raws):
            conds.append("album_artist IS NULL AND artist IS NULL")
        where = " OR ".join(f"({c})" for c in conds) if conds else "0"
        return self.conn.execute(
            f"SELECT * FROM tracks WHERE {where} ORDER BY album, track_number, title", args
        ).fetchall()

    def list_all_tracks(self) -> list[sqlite3.Row]:
        """Todas las pistas para la vista Canciones (artista → álbum → pista)."""
        return self.conn.execute(
            """SELECT * FROM tracks
               ORDER BY COALESCE(album_artist, artist) COLLATE NOCASE,
                        album, track_number, title"""
        ).fetchall()

    def search(self, query: str, limit: int = 100) -> list[sqlite3.Row]:
        query = query.strip()
        if not query:
            return []
        if self.has_fts:
            try:
                match = " ".join(f'"{tok}"*' for tok in query.split())
                return self.conn.execute(
                    """SELECT t.* FROM tracks t
                       JOIN tracks_fts f ON f.rowid = t.id
                       WHERE tracks_fts MATCH ? ORDER BY rank LIMIT ?""",
                    (match, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                pass
        like = f"%{query}%"
        return self.conn.execute(
            """SELECT * FROM tracks
               WHERE title LIKE ? OR artist LIKE ? OR album LIKE ?
               ORDER BY artist, album LIMIT ?""",
            (like, like, like, limit),
        ).fetchall()

    def mark_played(self, track_id: int) -> None:
        self.conn.execute(
            "UPDATE tracks SET play_count = play_count + 1, last_played = ? WHERE id = ?",
            (time.time(), track_id),
        )
        self.conn.commit()

    def stats(self) -> dict[str, int]:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT album) AS albums FROM tracks"
        ).fetchone()
        # Artistas por la MISMA identidad plegada que la vista Artistas.
        artists = len(set(self._artist_fold_map().values()))
        return {"tracks": row["n"], "albums": row["albums"], "artists": artists}
