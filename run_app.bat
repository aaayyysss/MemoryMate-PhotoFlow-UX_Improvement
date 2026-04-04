@echo off
setlocal

set PY=C:\Tools\MM_Runtime\.venv\Scripts\python.exe

if not exist "%PY%" (
  echo ERROR: Runtime python not found: %PY%
  echo Build the runtime first in C:\Tools\MM_Runtime
  pause
  exit /b 1
)

REM Force working directory to this code folder
pushd "%~dp0"

echo Using runtime: %PY%
"%PY%" -c "import sys; print('[BOOT] sys.executable =', sys.executable)"

REM Optional: ensure clean module resolution (prevents stray user-site)
set PYTHONNOUSERSITE=1

"%PY%" "main_qt.py"
set RC=%ERRORLEVEL%

popd
echo Exit code: %RC%
pause
exit /b %RC%