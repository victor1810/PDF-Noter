@echo off
setlocal
cd /d "%~dp0"

echo === PDF Noter: Tesseract OCR setup ===
echo.

set "DATA=%PUBLIC%\PdfNoter\tessdata"
if not exist "%DATA%" mkdir "%DATA%"
if errorlevel 1 goto fail

echo Language data folder: %DATA%

rem Prefer the files shipped with the project (no internet needed).
if exist "tessdata\eng.traineddata" copy /Y "tessdata\eng.traineddata" "%DATA%\eng.traineddata" >nul
if exist "tessdata\rus.traineddata" copy /Y "tessdata\rus.traineddata" "%DATA%\rus.traineddata" >nul

rem Download the fast models if they are missing (PowerShell handles TLS).
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $d=$env:PUBLIC+'\PdfNoter\tessdata'; foreach($lang in @('eng','rus')) { $f=Join-Path $d ($lang+'.traineddata'); if (-not (Test-Path $f) -or (Get-Item $f).Length -lt 1000000) { Write-Host ('Downloading '+$lang+'...'); Invoke-WebRequest -UseBasicParsing ('https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/'+$lang+'.traineddata') -OutFile $f } }"
if errorlevel 1 goto fail

where tesseract >nul 2>&1
if errorlevel 1 (
  echo.
  echo Tesseract engine not found. Installing UB Mannheim build...
  winget install --id UB-Mannheim.TesseractOCR -e --accept-source-agreements --accept-package-agreements
  if errorlevel 1 goto fail
) else (
  echo Tesseract engine: found
)

echo.
echo Done. OCR is available in the "Annotations" menu.
echo If Tesseract was just installed, restart PDF Noter once.
endlocal
exit /b 0

:fail
echo.
echo Setup failed. Install Tesseract-OCR manually and copy eng/rus traineddata
echo into: %DATA%
endlocal
exit /b 1
