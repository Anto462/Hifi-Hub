"""Playlists inteligentes: se definen por reglas que escribe el ususario y se recalculan al abrirlas.

Complementan las mezclas automáticas de «Para ti»: aquí el usuario decide los
criterios (género, año, escuchas, calidad…), el orden y el límite. Con el orden
«rota cada día» y los de rotación («más tiempo sin sonar», «menos
escuchadas») creado para evitar siempre repetir las mismas 5 canciones. Opcionalmente cada una puede
aparecer como mezcla en «Para ti», con el mismo estilo que las demás.

Las reglas se traducen a SQL con listas CERRADAS de campos, operadores
y órdenes, y todos los valores van como parámetros: un nombre o valor malicioso
nunca llega al texto de la consulta. De igual manera se mantienen en local tanto la playlist como la logica para crearlas.

Vive en su propia tabla (`smart_playlists`), creada por este módulo: el esquema
de db.py no cambia.
"""

from __future__ import annotations

import json
import random
import time
import unicodedata
from typing import Any

from hifihub.library.db import Library

SCHEMA = """
CREATE TABLE IF NOT EXISTS smart_playlists (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    spec        TEXT NOT NULL,
    created_at  REAL
);
"""

MAX_LIMIT = 5000


class SmartError(ValueError):
    """Regla incompleta o inválida (el mensaje se muestra tal cual en la UI)."""


# --- Catálogo (única fuente de verdad: la UI lo pide para construir el editor) ---

# `cols`: columnas de texto (varias = basta con que una cumpla); `expr`: expresión
# SQL fija. Ninguna sale de la entrada del usuario.
FIELDS: dict[str, dict[str, Any]] = {
    "title":       {"label": "Título", "type": "text", "cols": ["title"]},
    "artist":      {"label": "Artista", "type": "text", "cols": ["artist", "album_artist"]},
    "album":       {"label": "Álbum", "type": "text", "cols": ["album"]},
    "genre":       {"label": "Género", "type": "text", "cols": ["genre"]},
    "codec":       {"label": "Códec", "type": "text", "cols": ["codec"]},
    "year":        {"label": "Año", "type": "number", "expr": "CAST(substr(date,1,4) AS INTEGER)"},
    "duration":    {"label": "Duración", "type": "number", "unit": "min", "expr": "(duration / 60.0)"},
    "plays":       {"label": "Reproducciones", "type": "number", "expr": "play_count"},
    "sample_rate": {"label": "Frecuencia de muestreo", "type": "number", "unit": "kHz",
                    "expr": "(sample_rate / 1000.0)"},
    "bit_depth":   {"label": "Profundidad", "type": "number", "unit": "bits", "expr": "bit_depth"},
    "lossless":    {"label": "Sin pérdida (lossless)", "type": "bool", "expr": "lossless"},
    "last_played": {"label": "Última escucha", "type": "date", "expr": "last_played"},
    "added":       {"label": "Añadida a la biblioteca", "type": "date", "expr": "added_at",
                    "ops": ["in_last", "not_in_last"]},
}

OPS: dict[str, list[tuple[str, str]]] = {
    "text": [("contains", "contiene"), ("not_contains", "no contiene"), ("is", "es"),
             ("is_not", "no es"), ("starts", "empieza por")],
    "number": [("eq", "es"), ("ne", "no es"), ("gt", "mayor que"), ("lt", "menor que"),
               ("between", "entre")],
    "bool": [("true", "sí"), ("false", "no")],
    "date": [("in_last", "en los últimos (días)"), ("not_in_last", "hace más de (días)"),
             ("never", "nunca")],
}

ORDERS: list[tuple[str, str]] = [
    ("daily", "Aleatorio · rota cada día"),
    ("random", "Aleatorio cada vez"),
    ("stale", "Más tiempo sin sonar"),
    ("plays_asc", "Menos escuchadas"),
    ("plays_desc", "Más escuchadas"),
    ("added_desc", "Recién añadidas"),
    ("year_desc", "Año (más recientes)"),
    ("year_asc", "Año (más antiguas)"),
    ("artist", "Artista"),
    ("title", "Título"),
]
_ORDER_SQL = {
    "stale": "COALESCE(last_played, 0) ASC, play_count DESC",
    "plays_asc": "play_count ASC, COALESCE(last_played, 0) ASC",
    "plays_desc": "play_count DESC, last_played DESC",
    "added_desc": "added_at DESC",
    "year_desc": "CAST(substr(date,1,4) AS INTEGER) DESC",
    "year_asc": "CAST(substr(date,1,4) AS INTEGER) ASC",
    "artist": "COALESCE(album_artist, artist) COLLATE NOCASE, album, track_number",
    "title": "title COLLATE NOCASE",
}
_SHUFFLED = ("daily", "random")

# Puntos de partida para el editor (más opciones sin tener que pensar reglas).
TEMPLATES: list[dict[str, Any]] = [
    {"name": "Joyas olvidadas", "why": "Lo que escuchabas mucho y llevas tiempo sin poner",
     "spec": {"match": "all", "order": "stale", "limit": 50, "rules": [
         {"field": "plays", "op": "gt", "value": 2},
         {"field": "last_played", "op": "not_in_last", "value": 60}]}},
    {"name": "Descubrimientos", "why": "Pistas que apenas has escuchado; cambia cada día",
     "spec": {"match": "all", "order": "daily", "limit": 40, "rules": [
         {"field": "plays", "op": "lt", "value": 2}]}},
    {"name": "Recién llegadas sin estrenar", "why": "Lo que entró este mes y aún no has puesto",
     "spec": {"match": "all", "order": "added_desc", "limit": 0, "rules": [
         {"field": "added", "op": "in_last", "value": 30},
         {"field": "plays", "op": "eq", "value": 0}]}},
    {"name": "Alta resolución", "why": "Lossless por encima de 48 kHz",
     "spec": {"match": "all", "order": "daily", "limit": 0, "rules": [
         {"field": "lossless", "op": "true", "value": None},
         {"field": "sample_rate", "op": "gt", "value": 48}]}},
    {"name": "Tus favoritas", "why": "Las que más suenan",
     "spec": {"match": "all", "order": "plays_desc", "limit": 50, "rules": [
         {"field": "plays", "op": "gt", "value": 4}]}},
    {"name": "Temas largos", "why": "Más de 7 minutos: para escuchar sin prisa",
     "spec": {"match": "all", "order": "daily", "limit": 0, "rules": [
         {"field": "duration", "op": "gt", "value": 7}]}},
]


def catalog() -> dict[str, Any]:
    def ops_for(f):
        allowed = f.get("ops")
        return [o for o in OPS[f["type"]] if not allowed or o[0] in allowed]
    return {
        "fields": [{"key": k, "label": f["label"], "type": f["type"], "unit": f.get("unit", ""),
                    "ops": ops_for(f)} for k, f in FIELDS.items()],
        "orders": ORDERS,
        "templates": TEMPLATES,
    }


# --- Validación ------------------------------------------------------------------

def normalize_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Spec limpio y validado. Lanza SmartError con un mensaje para el usuario."""
    spec = spec or {}
    rules_in = spec.get("rules") or []
    if not isinstance(rules_in, list):
        raise SmartError("Reglas con un formato inválido.")
    rules = []
    for n, r in enumerate(rules_in, start=1):
        field = FIELDS.get((r or {}).get("field"))
        if field is None:
            raise SmartError(f"Regla {n}: campo desconocido.")
        op = r.get("op")
        valid_ops = [o for o, _ in OPS[field["type"]] if not field.get("ops") or o in field["ops"]]
        if op not in valid_ops:
            raise SmartError(f"Regla {n}: operador no válido para «{field['label']}».")
        rules.append({"field": r["field"], "op": op, "value": _clean_value(field, op, r.get("value"), n)})
    order = spec.get("order") if spec.get("order") in dict(ORDERS) else "daily"
    try:
        limit = max(0, min(MAX_LIMIT, int(spec.get("limit") or 0)))
    except (TypeError, ValueError):
        limit = 0
    return {"match": "any" if spec.get("match") == "any" else "all", "rules": rules,
            "order": order, "limit": limit, "foryou": bool(spec.get("foryou"))}


def _clean_value(field: dict, op: str, value: Any, n: int) -> Any:
    kind = field["type"]
    if kind == "bool" or op == "never":
        return None
    if kind == "text":
        text = str(value or "").strip()
        if not text:
            raise SmartError(f"Regla {n}: escribe un texto para «{field['label']}».")
        return text
    try:
        if op == "between":
            a, b = (float(x) for x in value)
            return [min(a, b), max(a, b)]
        num = float(value)
    except (TypeError, ValueError):
        raise SmartError(f"Regla {n}: «{field['label']}» necesita un número.") from None
    if kind == "date" and num <= 0:
        raise SmartError(f"Regla {n}: indica un número de días mayor que 0.")
    return num


# --- Reglas -> SQL ------------------------------------------------------------------

def _norm(value: Any) -> str:
    """Sin tildes ni mayúsculas (como el buscador de la Biblioteca): «musica» = «Música»."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFD", str(value))
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn").lower()


def _prepare(lib: Library) -> None:
    """Tabla propia + función de normalización en la conexión (ambas idempotentes)."""
    if getattr(lib, "_smart_ready", False):
        return
    lib.conn.executescript(SCHEMA)
    lib.conn.create_function("hh_norm", 1, _norm, deterministic=True)
    lib._smart_ready = True


def _rule_sql(rule: dict[str, Any], now: float) -> tuple[str, list]:
    field, op, v = FIELDS[rule["field"]], rule["op"], rule["value"]
    if field["type"] == "text":
        cols, needle = field["cols"], _norm(v)
        positive = {"contains": "instr(hh_norm({c}), ?) > 0", "is": "hh_norm({c}) = ?",
                    "starts": "instr(hh_norm({c}), ?) = 1"}
        negative = {"not_contains": "instr(hh_norm({c}), ?) = 0", "is_not": "hh_norm({c}) != ?"}
        tpl, joiner = (positive[op], " OR ") if op in positive else (negative[op], " AND ")
        return "(" + joiner.join(tpl.format(c=c) for c in cols) + ")", [needle] * len(cols)
    expr = field["expr"]
    if field["type"] == "bool":
        return f"COALESCE({expr}, 0) = ?", [1 if op == "true" else 0]
    if field["type"] == "date":
        if op == "never":
            return f"{expr} IS NULL", []
        cutoff = now - v * 86400
        if op == "in_last":
            return f"{expr} >= ?", [cutoff]
        return f"({expr} IS NULL OR {expr} < ?)", [cutoff]
    if op == "between":
        return f"{expr} BETWEEN ? AND ?", list(v)
    sym = {"eq": "=", "ne": "!=", "gt": ">", "lt": "<"}[op]
    return f"{expr} {sym} ?", [v]


_POSITIVE_TEXT = ("contains", "is", "starts")


def _where(spec: dict[str, Any]) -> tuple[str, list]:
    """WHERE de las reglas.

    Se ajustan las reglas para evitar que se utilizen and en tags que tiene mas sentdo usar or, por ejemplo
    un Artista contiene X + Artista contiene Y = canciones de X o de Y. Esto permite a la playlist ajustarse a las canciones de ambos artistas y actulizarse
    , si se unieran con un and como anteriormente pasaba la lista smart buscaria pistas en las cuales participen los dos a la vez.
    Las negativas por ejemplo «no contiene Hyde» y las numéricas o de fecha siguen con «y».
    Por ejemplo para excluir ambos valores o acotar un rango Ej: «año > 2000» y «año < 2010».
    """
    now = time.time()
    # Clave -> reglas que se unen con OR. Las agrupables comparten la clave de su
    # campo; el resto usa su índice. El dict conserva el orden, así
    # que cada grupo queda donde apareció su primera regla.
    buckets: dict[str | int, list[tuple[str, list]]] = {}
    for i, rule in enumerate(spec["rules"]):
        sql, p = _rule_sql(rule, now)
        grouped = (spec["match"] == "all" and FIELDS[rule["field"]]["type"] == "text"
                   and rule["op"] in _POSITIVE_TEXT)
        buckets.setdefault(rule["field"] if grouped else i, []).append((sql, p))
    parts, params = [], []
    for members in buckets.values():
        parts.append(members[0][0] if len(members) == 1
                     else "(" + " OR ".join(m[0] for m in members) + ")")
        for _, p in members:
            params += p
    if not parts:
        return "1", []
    return (" OR " if spec["match"] == "any" else " AND ").join(parts), params


def evaluate(lib: Library, spec: dict[str, Any], cols: str = "*", seed: int = 0) -> list:
    """Pistas que cumplen las reglas, ya ordenadas y limitadas.

    Los órdenes aleatorios se barajan en Python: «daily» con una semilla que cambia
    cada día al igual que las mezclas base aunque aca se puede guardar la semilla para repetirla por el tiempo determinado por el usuario y
    distinta por playlist; «random», en cada evaluación."""
    _prepare(lib)
    spec = normalize_spec(spec)
    where, params = _where(spec)
    order, limit = spec["order"], spec["limit"]
    if order in _ORDER_SQL:
        sql = f"SELECT {cols} FROM tracks WHERE {where} ORDER BY {_ORDER_SQL[order]}"
        if limit:
            sql += " LIMIT ?"
            params = params + [limit]
        return lib.conn.execute(sql, params).fetchall()
    rows = lib.conn.execute(f"SELECT {cols} FROM tracks WHERE {where} ORDER BY id", params).fetchall()
    if order == "daily":
        from hifihub.library.recommend import daily_seed
        random.Random(daily_seed() * 7919 + seed).shuffle(rows)
    else:
        random.shuffle(rows)
    return rows[:limit] if limit else rows


def count(lib: Library, spec: dict[str, Any]) -> dict[str, int]:
    """Cuántas cumplen las reglas (`matched`) y cuántas entran con el límite (`count`)."""
    _prepare(lib)
    spec = normalize_spec(spec)
    where, params = _where(spec)
    n = lib.conn.execute(f"SELECT COUNT(*) FROM tracks WHERE {where}", params).fetchone()[0]
    return {"matched": n, "count": min(n, spec["limit"]) if spec["limit"] else n}


def summary(spec: dict[str, Any]) -> str:
    """Descripción corta para tarjetas y para «Para ti»."""
    spec = normalize_spec(spec)
    rules = spec["rules"]
    if not rules:
        head = "Toda la biblioteca"
    elif len(rules) == 1:
        r = rules[0]
        f = FIELDS[r["field"]]
        op = dict(OPS[f["type"]])[r["op"]]
        val = r["value"]
        if isinstance(val, list):
            val = f"{val[0]:g}–{val[1]:g}"
        elif isinstance(val, float):
            val = f"{val:g}"
        head = " ".join(x for x in (f["label"], op, str(val) if val is not None else "", f.get("unit", "")) if x)
    else:
        head = f"{len(rules)} reglas ({'alguna' if spec['match'] == 'any' else 'todas'})"
    tail = dict(ORDERS)[spec["order"]]
    if spec["limit"]:
        tail += f" · máx. {spec['limit']}"
    return f"{head} · {tail}"


# --- Almacenamiento ---------------------------------------------------------------

def _decode(row) -> dict[str, Any]:
    try:
        spec = normalize_spec(json.loads(row["spec"]))
    except (ValueError, SmartError):
        spec = normalize_spec({})   # una fila dañada no rompe la lista
    return {"id": row["id"], "name": row["name"], "spec": spec}


def list_all(lib: Library) -> list[dict[str, Any]]:
    _prepare(lib)
    rows = lib.conn.execute("SELECT * FROM smart_playlists ORDER BY name COLLATE NOCASE").fetchall()
    return [_decode(r) for r in rows]


def get(lib: Library, smart_id: int) -> dict[str, Any] | None:
    _prepare(lib)
    row = lib.conn.execute("SELECT * FROM smart_playlists WHERE id = ?", (smart_id,)).fetchone()
    return _decode(row) if row else None


def save(lib: Library, name: str, spec: dict[str, Any], smart_id: int | None = None) -> int:
    """Crea (smart_id None) o actualiza. Devuelve el id. Valida antes de guardar."""
    _prepare(lib)
    name = (name or "").strip()
    if not name:
        raise SmartError("Ponle un nombre a la playlist inteligente.")
    body = json.dumps(normalize_spec(spec))
    if smart_id is None:
        cur = lib.conn.execute(
            "INSERT INTO smart_playlists (name, spec, created_at) VALUES (?, ?, ?)",
            (name, body, time.time()))
        smart_id = cur.lastrowid
    else:
        lib.conn.execute("UPDATE smart_playlists SET name = ?, spec = ? WHERE id = ?",
                         (name, body, smart_id))
    lib.conn.commit()
    return int(smart_id)


def delete(lib: Library, smart_id: int) -> None:
    _prepare(lib)
    lib.conn.execute("DELETE FROM smart_playlists WHERE id = ?", (smart_id,))
    lib.conn.commit()


# --- «Para ti» -----------------------------------------------------------------------

def foryou_mixes(lib: Library, seed: int | None = None) -> list[dict[str, Any]]:
    """Las inteligentes marcadas «Mostrar en Para ti», empaquetadas como mezclas.

    Pasan por el mismo `_mix` que las automáticas: mismo mínimo de pistas (si no
    llegan, no se muestran), mismo techo y misma rotación diaria."""
    from hifihub.library.recommend import _COLS, _mix, daily_seed

    seed = daily_seed() if seed is None else seed
    out = []
    for sp in list_all(lib):
        if not sp["spec"]["foryou"]:
            continue
        rows = evaluate(lib, sp["spec"], cols=_COLS, seed=sp["id"])
        m = _mix(f"smart:{sp['id']}", sp["name"], summary(sp["spec"]), "smart", rows,
                 seed, shuffle=sp["spec"]["order"] in _SHUFFLED)
        if m:
            out.append(m)
    return out
