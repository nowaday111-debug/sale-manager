@echo off
chcp 65001 >nul
rem Keo-tha (hoac chon) cac file Bang ke Excel cu vao file nay de nhap lich su.
cd /d "%~dp0backend"
call .venv\Scripts\activate.bat
python -m scripts.import_excel %*
pause
