@echo off
setlocal
cd /d "%~dp0"

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
)
if not defined PY (
  echo Python not found. Install Python 3.12 from python.org and retry.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  "%PY%" -m venv .venv
  if errorlevel 1 goto fail
)
call .venv\Scripts\activate.bat

echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail

echo Building executable...
python -m PyInstaller --noconfirm --clean --windowed ^
  --name "PDF Noter" ^
  --icon resources\app.ico ^
  --add-data "resources\app.ico;resources" ^
  --add-data "tessdata;tessdata" ^
  src\main.py
if errorlevel 1 goto fail

echo.
echo Done: dist\PDF Noter\PDF Noter.exe
echo Note: OCR also needs the Tesseract engine (run install_tesseract.bat once).
endlocal
exit /b 0

:fail
echo.
echo Build failed. Check Python installation and internet connection.
endlocal
exit /b 1
