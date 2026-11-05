@echo off
chcp 65001 >nul
cd /d "%~dp0backend"
if not exist .venv (echo Chua cai dat - chay cai-dat.bat truoc & pause & exit /b 1)
call .venv\Scripts\activate.bat
echo Dang mo Sale-manager tai http://127.0.0.1:8000  (dong cua so nay = tat phan mem)
echo May khac trong mang noi bo mo: http://[dia chi IP may nay]:8000
start "" http://127.0.0.1:8000/
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
