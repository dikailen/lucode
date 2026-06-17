@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE="

if defined LUCODE_PYTHON (
  if exist "%LUCODE_PYTHON%" set "PYTHON_EXE=%LUCODE_PYTHON%"
)

if not defined PYTHON_EXE (
  if exist "D:\develop\Data_anaconda2024\envs\agents-demo\python.exe" (
    set "PYTHON_EXE=D:\develop\Data_anaconda2024\envs\agents-demo\python.exe"
  )
)

if not defined PYTHON_EXE (
  set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" -c "import PySide6, qasync" >nul 2>nul
if errorlevel 1 (
  echo Lucode GUI dependencies are missing in: %PYTHON_EXE%
  echo Set LUCODE_PYTHON to your agents-demo python.exe or install GUI extras.
  echo Example:
  echo   set LUCODE_PYTHON=D:\develop\Data_anaconda2024\envs\agents-demo\python.exe
  pause
  exit /b 2
)

"%PYTHON_EXE%" -m lucode.gui %*
