# Este archivo empaqueta HiFi Hub en un .exe con sus binarios y assets (Icono).
#
# Los binarios pesados de herramientas/ (ffmpeg, libmpv, rsgain, fpcalc) NO se
# incrustan en el exe: se copian al lado, en dist/HiFiHub/herramientas/. paths.py
# los resuelve por ruta relativa al ejecutable, así que funcionan igual. Debes colocarlos siempre en una carpeta al lado del .exe
# A continuacion adjunto el comando, con eso no tendras que escribir las specs sino solo solicitar a pyinstaller que use hifihub.spec como "guia" para generar el exe.
# Comando para build:  pyinstaller hifihub.spec --noconfirm

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "hifihub" / "ui" / "web"), "hifihub/ui/web"),
    (str(ROOT / "hifihub" / "data"), "hifihub/data"),
    (str(ROOT / "herramientas"), "herramientas"),
    (str(ROOT / "assets" / "mi_icono.ico"), "assets"),  # icono de ventana (runtime)
]
# librosa trae datos y submódulos que hay que recolectar explícitamente.
datas += collect_data_files("librosa")
# pedalboard (motor DSP offline, libs nativas de efectos y VST3.
datas += collect_data_files("pedalboard")

hiddenimports = (
    collect_submodules("librosa")
    + collect_submodules("sklearn")
    + collect_submodules("pedalboard")
    + ["numpy", "scipy.signal", "soundfile", "mutagen", "PIL"]
)
# matchering (mastering por referencia) arrastra numba/resampy,
# muy pesados para PyInstaller. La app degrada con un mensaje claro si falta.

a = Analysis(
    ["app.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,   # Los binarios se estan pasando por aparte
    name="HiFiHub",          # Nombre del exe
    console=False,           # app de ventana, sin consolas
    disable_windowed_traceback=False,
    icon=str(ROOT / "assets" / "mi_icono.ico"),  # icono del .exe y de la ventana
)
coll = COLLECT(
    exe, a.binaries, a.datas, # Aca se deja al exe y sus archivos necesarios externos
    name="HiFiHub",
)
