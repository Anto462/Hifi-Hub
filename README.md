# HiFi Hub

A Hi-Fi music library manager and player for Windows. Import, organize, and play your music from a single hub with additional tools designed to maximize your audio enjoyment.

Windows desktop app. Everything runs locally.

**English** · [Español](README.es.md)

![HiFi Hub](.)

<details>
<summary>More screenshots</summary>

![Library view](.)
![Now playing](.)
![Playlist](.)
![Import](.)

</details>

---

## Download

**[Download the latest release](https://github.com/Anto462/Hifi-Hub/releases/tag/Stable)**

### Requirements

- Windows 10 or 11 (64-bit)
- [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) 
- [Python 3.11+](https://www.python.org/downloads/release/python-3110/)
- [.NET Framework 4.8](https://dotnet.microsoft.com/download/dotnet-framework/net48)

## For the User

You can download the latest release and start HiFiHub.exe.

## Developer - Getting Started

### 1. Extract the tools (Or you can download each listed tool on your own)

Inside of `herramientas/` there are two compressed files:

- `exe_tools.rar` -> ffmpeg.exe, ffprobe.exe, rsgain.exe, fpcalc.exe
- `dlls.rar` -> libmpv-2.dll and the Visual C++ DLLs

**Extract both directly inside the `herramientas/` folder**, without creating subfolders. The final result should look like this:

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

These binaries are compressed because some exceed the 100 MB limit, which prevents me from uploading them directly to GitHub.
They are third-party tools with their own licenses: see THIRD-PARTY.md (TERCEROS.md).

### 2. Install dependencies

Create a virtual environment with Python 3.11+ (VENV) and install the project in editable mode. Dependencies are declared in `pyproject.toml`.

Extras:

- `studio` -> offline DSP engine (pedalboard, for example)
- `dev` -> pytest

### 3. Run

    python app.py

## Generate the executable

    pyinstaller hifihub.spec --noconfirm --clean

The output will be located in `dist/HiFiHub/`.

> **Important:** the `.spec` file copies the entire `herramientas/` folder. If you
> build without having extracted the .rar files, PyInstaller will package
> the .rar files themselves and the executable will fail because it won't find `ffmpeg.exe`.

## Where your data is stored

Library, settings, and presets live in `C:\Users\<user>\.hifihub`.
Everything is stored locally, and play counts, playlists created in "For You", etc., are calculated internally.

## License

Hifi-Hub: **GPL v3** (see `LICENSE.txt`).
Third-party tools: each retains its own license (see `TERCEROS.md`).