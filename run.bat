@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" src\main.py %*
) else if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" src\main.py %*
) else (
  python src\main.py %*
)
if errorlevel 1 (
  echo.
  echo Failed to start. Install dependencies first:
  echo   python -m pip install -r requirements.txt
)
endlocal
