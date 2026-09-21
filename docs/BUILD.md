# Building Muesli for Windows

## One command

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

Creates a venv, installs everything, runs the tests, generates the icon, freezes
with PyInstaller, smoke-tests the frozen CLI, and builds the installer.

Flags: `-SkipTests`, `-SkipInstaller`, `-Python C:\Python312\python.exe`.

## Prerequisites

| | |
|---|---|
| Python | 3.10–3.12 (3.13 works; `faster-whisper` wheels lag on new releases) |
| Inno Setup 6 | `winget install JRSoftware.InnoSetup` — only for the installer |
| Visual C++ runtime | Already present on Windows 10/11 |

## Step by step

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-optional.txt -r requirements-dev.txt

$env:QT_QPA_PLATFORM = "offscreen"
pytest tests -q
Remove-Item Env:\QT_QPA_PLATFORM

python packaging\make_icon.py
python -m PyInstaller --noconfirm --clean packaging\muesli.spec
.\dist\Muesli\muesli-cli.exe info

& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" packaging\muesli.iss
```

## Decisions worth knowing

**One-dir, not one-file.** A one-file build unpacks ~200 MB to `%TEMP%` on every
launch. For an app whose whole point is responding instantly to a keypress that
is the wrong trade. The installer hides the folder.

**Models are not bundled.** They download on first use into
`%LOCALAPPDATA%\Muesli\cache\models`, exactly as macOS Muesli does. Bundling
`large-v3` would make a 4 GB installer that most users would not need.

**UPX is off.** It corrupts the CTranslate2 and ONNX Runtime DLLs and the
failure appears later as an unexplained load error.

**Qt modules are excluded.** WebEngine, Quick, QML, Multimedia, Charts and the
rest are not used and cost ~90 MB.

**Two executables, one bundle.** `Muesli.exe` is windowed (no console flash);
`muesli-cli.exe` is a console app so scripts can read its JSON. `COLLECT` puts
both in one folder and shares the dependencies.

## Expected sizes

| | |
|---|---|
| `dist\Muesli\` | ~300–380 MB |
| `Muesli-Setup-1.0.2.exe` | ~110–140 MB |
| Plus models, after first run | 0.5–3 GB depending on choice |

## GPU builds

`faster-whisper` uses CTranslate2, which needs cuBLAS and cuDNN on the PATH for
CUDA. The simplest route on a user machine:

```powershell
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Without them the app detects the failure and falls back to CPU `int8` rather
than crashing — see `stt/whisper_fw.py`. Force either mode with
`muesli-cli config set win_compute_device '"cpu"'`.

## CI

`.github/workflows/build.yml`:

- **logic** (ubuntu) — ruff, plus the OS-independent tests. ~1 minute.
- **windows** (windows-latest) — imports every module on Windows, runs the full
  suite, builds the exe, asserts the frozen CLI runs and reports `frozen=true`,
  builds the installer, uploads both as artifacts. Tagging `v*` attaches the
  installer to a GitHub release.

## Troubleshooting the build

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError` only in the frozen build | Lazily imported module missing from `hidden` in `muesli.spec` |
| Frozen app exits instantly, no window | Run `dist\Muesli\muesli-cli.exe info` to see the real traceback |
| `ISCC.exe` not found | Inno Setup 6 not installed, or installed per-user in a different path |
| DLL load failed for ctranslate2 | UPX was enabled, or the VC++ runtime is missing |
