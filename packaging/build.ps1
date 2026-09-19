<#
.SYNOPSIS
  Build Muesli for Windows: exe, then installer.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
  powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -SkipInstaller
#>
param(
    [switch]$SkipInstaller,
    [switch]$SkipTests,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "==> Python environment" -ForegroundColor Cyan
& $Python -m venv .venv
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip wheel
& $py -m pip install -r requirements.txt -r requirements-optional.txt -r requirements-dev.txt

if (-not $SkipTests) {
    Write-Host "==> Tests" -ForegroundColor Cyan
    $env:QT_QPA_PLATFORM = "offscreen"
    & $py -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
    Remove-Item Env:\QT_QPA_PLATFORM
}

Write-Host "==> Icon" -ForegroundColor Cyan
& $py packaging\make_icon.py

Write-Host "==> PyInstaller" -ForegroundColor Cyan
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $py -m PyInstaller --noconfirm --clean packaging\muesli.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = "dist\Muesli\Muesli.exe"
if (-not (Test-Path $exe)) { throw "expected $exe was not produced" }
Write-Host "    built $exe" -ForegroundColor Green

Write-Host "==> Smoke test (CLI in the frozen build)" -ForegroundColor Cyan
& "dist\Muesli\muesli-cli.exe" info | Out-Host
if ($LASTEXITCODE -ne 0) { throw "frozen CLI failed to run" }

if (-not $SkipInstaller) {
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $iscc) {
        Write-Warning "Inno Setup 6 not found - skipping the installer."
        Write-Warning "Install it with:  winget install JRSoftware.InnoSetup"
    } else {
        Write-Host "==> Installer" -ForegroundColor Cyan
        & $iscc packaging\muesli.iss
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
        Get-ChildItem dist\Muesli-Setup-*.exe | ForEach-Object {
            Write-Host ("    {0}  ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB)) -ForegroundColor Green
        }
    }
}

Write-Host "`nDone." -ForegroundColor Green
