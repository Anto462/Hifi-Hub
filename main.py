"""Punto de entrada que use al inicio del desarollo (LOL). La lógica vive ahora en el paquete hifihub/. Igualmente se puede utilizar para comandos especificos. Por ejemplo:

Uso:  python main.py audio <url>   |   python main.py --help
"""

from hifihub.cli import app

if __name__ == "__main__":
    # Si se corre desde main al final invocara a app.py por lo que actualmente esta mas como punto de entrada alternativo otra cosa y por la naturalidad de tener un main.
    app()
