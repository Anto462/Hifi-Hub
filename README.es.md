# HiFi Hub

Gestor y reproductor de bibliotecas musicales HIFI para Windows. Importa, organiza y reproduce tu música desde un mismo HUB con herramientas extras pensadas en maximizar el disfrute del audio.

Proyecto personal de software libre (ver LICENSE.txt).
Aplicación de escritorio para Windows. Todo funciona en local.

[English](README.md) · **Español**

<img width="800" alt="image" src="https://github.com/user-attachments/assets/9cb64bb4-b2ca-4a63-9277-b8c7a76a1059" />

<details>
<summary>Más capturas</summary>

<p align="center">
  <a href="https://github.com/user-attachments/assets/2b94d8ff-5c7c-4830-8928-818a68281fb4">
    <img src="https://github.com/user-attachments/assets/2b94d8ff-5c7c-4830-8928-818a68281fb4" width="200" alt="Library view" />
  </a>
  &nbsp;&nbsp;
  <a href="https://github.com/user-attachments/assets/d2f71636-947f-4f61-a293-e2e184ebfe53">
    <img src="https://github.com/user-attachments/assets/d2f71636-947f-4f61-a293-e2e184ebfe53" width="200" alt="Now playing" />
  </a>
  &nbsp;&nbsp;
  <a href="https://github.com/user-attachments/assets/09c1186f-fbb9-4db3-953a-9da37cd52802">
    <img src="https://github.com/user-attachments/assets/09c1186f-fbb9-4db3-953a-9da37cd52802" width="200" alt="Playlist" />
  </a>
  &nbsp;&nbsp;
  <a href="https://github.com/user-attachments/assets/a5bc8bb0-5f78-4b76-9bfc-ee688c8b11db">
    <img src="https://github.com/user-attachments/assets/a5bc8bb0-5f78-4b76-9bfc-ee688c8b11db" width="200" alt="Import" />
  </a>
</p>

</details>

---

## Descarga

**[Descargar la última versión](https://github.com/Anto462/Hifi-Hub/releases/tag/Stable)**

### Requisitos

- Windows 10 u 11 (64 bits)
- [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
- [Python 3.11+](https://www.python.org/downloads/release/python-3110/)
- [.NET Framework 4.8](https://dotnet.microsoft.com/download/dotnet-framework/net48)

## Para el usuario

Puedes simplemente descargar el release mas reciente y ejecutar el ejecutable.

## Desarrollador - Primeros pasos

### 1. Descomprimir las herramientas (O puedes descargar cada herramienta listada por tu cuenta)

Dentro de `herramientas/` hay dos archivos comprimidos:

- `exe_tools.rar` -> ffmpeg.exe, ffprobe.exe, rsgain.exe, fpcalc.exe
- `dlls.rar` -> libmpv-2.dll y las DLL de Visual C++

**Descomprime ambos dentro de la propia carpeta `herramientas/`**, sin crear
subcarpetas. El resultado debe quedar asi:

    herramientas/
      ffmpeg.exe
      ffprobe.exe
      rsgain.exe
      fpcalc.exe
      libmpv-2.dll
      msvcp140.dll
      vcruntime140.dll
      vcruntime140_1.dll
      presets/

Estos binarios van comprimidos porque algunos superan el limite de 100 MB lo cual no me permite subirlos directamente a GitHub. 
Son herramientas de terceros con sus propias licencias: ver `TERCEROS.md`.

### 2. Instalar las dependencias

Crea un entorno virtual con Python 3.11+ (VENV) e instala el proyecto en modo
editable. Las dependencias estan declaradas en `pyproject.toml`.

Extras opcionales:

- `studio` -> motor DSP offline (pedalboard por ejemplo)
- `dev` -> pytest

### 3. Ejecutar

    python app.py

## Generar el ejecutable

    pyinstaller hifihub.spec --noconfirm --clean

El resultado queda en `dist/HiFiHub/`.

> **Importante:** el `.spec` copia la carpeta `herramientas/` completa. Si
> construyes **sin haber descomprimido los .rar**, PyInstaller empaquetara los
> propios .rar y el ejecutable fallara al no encontrar `ffmpeg.exe`.

## Donde se guardan tus datos

Biblioteca, ajustes y presets viven en `C:\Users\<usuario>\.hifihub`.
Todo es guardado de forma local y el conteo de reproducciones, playlist creadas en "Para Ti", Etc. Se calcula de forma interna.

## Licencia

Hifi-Hub: **GPL v3** (ver `LICENSE.txt`).
Herramientas de terceros: cada una conserva su licencia (ver `TERCEROS.md`).
