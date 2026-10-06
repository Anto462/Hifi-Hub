"""HiFi Hub — Gestor y reproductor de bibliotecas musicales Hifi."""

__version__ = "1.0.0"

# Si el usuario actualizó yt-dlp desde la app, la copia
# nueva vive en CONFIG_DIR/overrides/ y debe tener precedencia sobre la del
# bundle/venv. Hay que insertarla en sys.path ANTES de que cualquier módulo haga
# `import yt_dlp`, y todos los submódulos de hifihub pasan por este __init__.
def _bootstrap_overrides() -> None:
    # Imports de sys y os
    import os
    import sys

    home = os.environ.get("HIFIHUB_HOME") or os.path.join(os.path.expanduser("~"), ".hifihub")
    overrides = os.path.join(home, "overrides")
    if os.path.isdir(overrides) and overrides not in sys.path:
        sys.path.insert(0, overrides)


_bootstrap_overrides()
