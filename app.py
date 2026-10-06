"""Punto de inicio de la aplicación de escritorio (usado con PyInstaller).

Para ejecutar desde terminal directamente la UI pywebview usa el comando `python app.py` o empaqueta y usa el .exe empaquetado.
"""

from hifihub.win_console import hide_child_consoles
from hifihub.ui.app import run

if __name__ == "__main__":
    # Se invoca para ocultar las ventanas de consola de ffmpeg/fpcalc/etc. en el .exe (Windows).
    hide_child_consoles()
    # Se invoca para llamar a la UI y correr el app.
    run()
