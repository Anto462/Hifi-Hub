"""
Mezclas y «Para ti»

Las recomendaciones salen de datos que la
biblioteca ya tiene (género, año, número de escuchas, última escucha, fecha de
alta). Esto mantiene tus gustos en local, lo cual ta bien.

Ninguna sección está cableada a un género ni a una época concretos: cada bloque se construye a partir de lo que esa biblioteca contenga, y si no tiene data suficiente no recomienda,
Esto para evitar mostrar algo medio vacío. 

El orden se baraja con una semilla DIARIA, de modo que las mezclas
cambian cada día pero no se reordenan al repintar la pantalla.
"""

from __future__ import annotations

import random
import time
from typing import Any

# Una mezcla con menos pistas que esto no merece existir: se vería pobre y no da
# para una escucha. Es el umbral que hace que la pantalla se adapte sola.
MIN_MIX = 8
MAX_TRACKS = 60          # techo por mezcla (no hace falta más para escuchar)
MAX_GENRE_MIXES = 6      # evita 40 tarjetas en bibliotecas con muchos géneros
STALE_DAYS = 14          # "hace tiempo que no escuchas a…"


def daily_seed() -> int:
    """Semilla que cambia una vez al día (mezclas estables dentro del día)."""
    return int(time.time() // 86400)


def _shuffled(items: list, seed: int) -> list:
    out = list(items)
    random.Random(seed).shuffle(out)
    return out


def _mix(key: str, title: str, subtitle: str, kind: str, rows: list,
         seed: int, shuffle: bool = True) -> dict[str, Any] | None:
    """Empaqueta una mezcla, o None si no reúne el mínimo."""
    if len(rows) < MIN_MIX:
        return None
    picked = _shuffled(rows, seed) if shuffle else list(rows)
    picked = picked[:MAX_TRACKS]
    cover = next((r["cover_path"] for r in picked if r["cover_path"]), None)
    return {
        "key": key,
        "title": title,
        "subtitle": subtitle,
        "kind": kind,
        "count": len(picked),
        "total": len(rows),
        "cover_path": cover,
        "track_ids": [r["id"] for r in picked],
    }


_COLS = "id, title, artist, album_artist, album, genre, date, cover_path, play_count, last_played"


def build_mixes(lib, seed: int | None = None) -> list[dict[str, Any]]:
    """Construye todas las mezclas que la biblioteca pueda sostener."""
    seed = daily_seed() if seed is None else seed
    c = lib.conn
    mixes: list[dict[str, Any]] = []

    played = c.execute("SELECT COUNT(*) n FROM tracks WHERE play_count > 0").fetchone()["n"]

    # En rotación
    if played:
        rows = c.execute(
            f"SELECT {_COLS} FROM tracks WHERE play_count > 0 "
            "ORDER BY play_count DESC, last_played DESC LIMIT ?", (MAX_TRACKS,)
        ).fetchall()
        m = _mix("rotacion", "En rotación", "Lo que más estás escuchando",
                 "rotation", rows, seed, shuffle=False)
        if m:
            mixes.append(m)

    # Mezclas por género
    for g in _top_genres(c):
        rows = c.execute(
            f"SELECT {_COLS} FROM tracks WHERE LOWER(TRIM(genre)) = ? ", (g["key"],)
        ).fetchall()
        m = _mix(f"genero:{g['key']}", g["label"], f"{len(rows)} pistas de tu biblioteca",
                 "genre", rows, seed)
        if m:
            mixes.append(m)

    # Sin estrenar
    if played:
        rows = c.execute(f"SELECT {_COLS} FROM tracks WHERE play_count = 0").fetchall()
        m = _mix("sin-estrenar", "Sin estrenar", "Nunca les has dado al play",
                 "fresh", rows, seed)
        if m:
            mixes.append(m)

    # Décadas
    for dec in _decades(c):
        rows = c.execute(
            f"SELECT {_COLS} FROM tracks WHERE CAST(substr(date,1,4) AS INTEGER) "
            "BETWEEN ? AND ?", (dec, dec + 9)
        ).fetchall()
        m = _mix(f"decada:{dec}", f"{dec}s", "Cápsula del tiempo", "decade", rows, seed)
        if m:
            mixes.append(m)

    # uelve a… 
    artist = _stale_artist(c)
    if artist:
        rows = c.execute(
            f"SELECT {_COLS} FROM tracks WHERE COALESCE(album_artist, artist) = ?",
            (artist,)
        ).fetchall()
        m = _mix(f"vuelve:{artist}", f"Vuelve a {artist}",
                 "Hace tiempo que no lo pones", "revisit", rows, seed)
        if m:
            mixes.append(m)

    return mixes


def _top_genres(c) -> list[dict[str, Any]]:
    """Géneros con material suficiente, ordenados por peso en la biblioteca.

    Se agrupan en minúsculas para que «Rock», «rock» y «ROCK» sean el mismo, y se
    muestra la grafía más frecuente. No hay lista blanca de géneros: vale
    cualquiera que traigan los tags.
    """
    rows = c.execute(
        "SELECT LOWER(TRIM(genre)) AS key, COUNT(*) AS n FROM tracks "
        "WHERE genre IS NOT NULL AND TRIM(genre) != '' "
        "GROUP BY LOWER(TRIM(genre)) HAVING n >= ? ORDER BY n DESC LIMIT ?",
        (MIN_MIX, MAX_GENRE_MIXES),
    ).fetchall()
    out = []
    for r in rows:
        label = c.execute(
            "SELECT genre, COUNT(*) n FROM tracks WHERE LOWER(TRIM(genre)) = ? "
            "GROUP BY genre ORDER BY n DESC LIMIT 1", (r["key"],)
        ).fetchone()
        out.append({"key": r["key"], "label": (label["genre"] if label else r["key"]),
                    "n": r["n"]})
    return out


def _decades(c) -> list[int]:
    """Décadas presentes con material suficiente (acepta cualquier época)."""
    rows = c.execute(
        "SELECT (CAST(substr(date,1,4) AS INTEGER) / 10) * 10 AS dec, COUNT(*) n "
        "FROM tracks WHERE date IS NOT NULL AND length(date) >= 4 "
        "AND CAST(substr(date,1,4) AS INTEGER) BETWEEN 1900 AND 2100 "
        "GROUP BY dec HAVING n >= ? ORDER BY n DESC LIMIT 3",
        (MIN_MIX,),
    ).fetchall()
    return [int(r["dec"]) for r in rows if r["dec"]]


def _stale_artist(c) -> str | None:
    """Artista con escuchas reales pero abandonado hace tiempo."""
    cutoff = time.time() - STALE_DAYS * 86400
    row = c.execute(
        "SELECT COALESCE(album_artist, artist) AS name, SUM(play_count) AS plays, "
        "MAX(COALESCE(last_played, 0)) AS last "
        "FROM tracks WHERE COALESCE(album_artist, artist) IS NOT NULL "
        "GROUP BY COALESCE(album_artist, artist) "
        "HAVING plays > 0 AND last < ? AND COUNT(*) >= ? "
        "ORDER BY plays DESC LIMIT 1",
        (cutoff, MIN_MIX),
    ).fetchone()
    return row["name"] if row else None


def headline_stats(lib) -> dict[str, Any]:
    """Cifras reales para la cabecera de la pantalla."""
    c = lib.conn
    r = c.execute(
        "SELECT COUNT(*) tracks, COALESCE(SUM(play_count), 0) plays, "
        "COUNT(DISTINCT album) albums FROM tracks"
    ).fetchone()
    top = c.execute(
        "SELECT title, COALESCE(album_artist, artist) AS artist, play_count "
        "FROM tracks WHERE play_count > 0 ORDER BY play_count DESC LIMIT 1"
    ).fetchone()
    return {
        "tracks": r["tracks"],
        "plays": r["plays"],
        "albums": r["albums"],
        "top": dict(top) if top else None,
    }


# --- Tiras de «recientes» -----------------------------------------------------
# Formato distinto al de las mezclas a propósito: son atajos para continuar donde
# lo dejaste, no colecciones para explorar.

MIN_STRIP = 2            # umbral bajo a propósito: un carril horizontal con 2
                         # elementos se lee bien, y quien acaba de importar dos
                         # álbumes debe ver justo eso en su pantalla de inicio.


def recent_artists(lib, limit: int = 12) -> list[dict[str, Any]]:
    """Artistas escuchados hace menos tiempo, con el MISMO plegado de
    colaboraciones que la vista Artistas.
    """
    fold = lib._artist_fold_map()
    rows = lib.conn.execute(
        "SELECT COALESCE(album_artist, artist) AS raw, MAX(last_played) AS last, "
        "SUM(play_count) AS plays, MAX(cover_path) AS cover_path, COUNT(*) AS n "
        "FROM tracks WHERE last_played IS NOT NULL "
        "GROUP BY COALESCE(album_artist, artist)"
    ).fetchall()

    agg: dict[Any, dict[str, Any]] = {}
    for r in rows:
        key = fold.get(r["raw"], r["raw"])
        if not key:
            continue
        cur = agg.setdefault(key, {"name": key, "last": 0, "plays": 0,
                                   "cover_path": None, "tracks": 0})
        cur["last"] = max(cur["last"], r["last"] or 0)
        cur["plays"] += r["plays"] or 0
        cur["tracks"] += r["n"]
        if not cur["cover_path"] and r["cover_path"]:
            cur["cover_path"] = r["cover_path"]

    out = sorted(agg.values(), key=lambda d: d["last"], reverse=True)
    return out[:limit]


def recent_albums(lib, limit: int = 12) -> list[dict[str, Any]]:
    """Álbumes escuchados más recientemente."""
    rows = lib.conn.execute(
        "SELECT album, MAX(last_played) AS last, COUNT(*) AS tracks, "
        "MAX(cover_path) AS cover_path, "
        "CASE WHEN COUNT(DISTINCT COALESCE(album_artist, artist)) > 1 "
        "     THEN 'Varios artistas' ELSE MAX(COALESCE(album_artist, artist)) END AS artist "
        "FROM tracks WHERE last_played IS NOT NULL "
        "AND album IS NOT NULL AND TRIM(album) != '' "
        "GROUP BY album ORDER BY last DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def recently_added_albums(lib, limit: int = 12) -> list[dict[str, Any]]:
    """Últimos álbumes que entraron en la biblioteca.
    """
    rows = lib.conn.execute(
        "SELECT album, MAX(added_at) AS added, COUNT(*) AS tracks, "
        "MAX(cover_path) AS cover_path, "
        "CASE WHEN COUNT(DISTINCT COALESCE(album_artist, artist)) > 1 "
        "     THEN 'Varios artistas' ELSE MAX(COALESCE(album_artist, artist)) END AS artist "
        "FROM tracks WHERE album IS NOT NULL AND TRIM(album) != '' "
        "GROUP BY album ORDER BY added DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def build_strips(lib) -> dict[str, list[dict[str, Any]]]:
    """Las tres tiras, ya filtradas por el mínimo para no mostrarlas a medias."""
    def keep(items):
        return items if len(items) >= MIN_STRIP else []

    return {
        "artists": keep(recent_artists(lib)),
        "albums": keep(recent_albums(lib)),
        "added": keep(recently_added_albums(lib)),
    }
