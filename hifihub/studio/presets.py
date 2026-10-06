"""Cadenas de efectos default.

Ajustes basicos para distintas preferencia que puede tener un usuario, lo normal seria que cada quien agregue sus preferidos. El original nunca cambia.
"""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "calidez": {
        "label": "Calidez",
        "description": "Realce suave de graves-medios y agudos ligeramente redondeados.",
        "chain": [
            {"type": "lowshelf", "cutoff_hz": 200, "gain_db": 1.5, "q": 0.7},
            {"type": "highshelf", "cutoff_hz": 9000, "gain_db": -1.0, "q": 0.7},
        ],
    },
    "claridad": {
        "label": "Claridad vocal",
        "description": "Presencia en la voz (2–4 kHz) y aire en agudos.",
        "chain": [
            {"type": "peak", "cutoff_hz": 3000, "gain_db": 2.0, "q": 1.2},
            {"type": "highshelf", "cutoff_hz": 10000, "gain_db": 1.5, "q": 0.7},
        ],
    },
    "nivelar": {
        "label": "Nivelar dinámica",
        "description": "Compresión suave + limitador transparente para escucha casual.",
        "chain": [
            {"type": "compressor", "threshold_db": -20, "ratio": 2.0, "attack_ms": 10, "release_ms": 150},
            {"type": "limiter", "threshold_db": -1.0, "release_ms": 100},
        ],
    },
    "subgrave": {
        "label": "Refuerzo de subgraves",
        "description": "Extensión en graves profundos con paso-alto para limpiar retumbe.",
        "chain": [
            {"type": "highpass", "cutoff_hz": 25},
            {"type": "lowshelf", "cutoff_hz": 60, "gain_db": 2.5, "q": 0.7},
        ],
    },
}


def get_preset_chain(name: str) -> list[dict]:
    preset = PRESETS.get(name)
    if not preset:
        raise KeyError(f"Preset desconocido: {name!r}")
    # Copia profunda ligera para que la UI pueda editar sin mutar el preset.
    return [dict(effect) for effect in preset["chain"]]


def list_presets() -> list[dict]:
    return [{"name": k, "label": v["label"], "description": v["description"]} for k, v in PRESETS.items()]
