@echo off
setlocal
cd /d "%~dp0"
set "POLITICA_PROJECT_ROOT=%CD%"
where uv >nul 2>&1
if errorlevel 1 (
  echo Politica needs the uv Python package manager.
  echo Install it once with: python -m pip install uv
  echo Then run this file again.
  pause
  exit /b 1
)
uv sync --locked --python 3.12 --no-editable
if errorlevel 1 (
  echo Politica could not prepare its local environment.
  pause
  exit /b 1
)
uv run --no-sync python -m politica_erd.app.cli
if errorlevel 1 pause
