@echo off
chcp 65001 >nul
cd /d "%~dp0backend"
echo === CAI DAT SALE-MANAGER (chay 1 lan) ===
where python >nul 2>nul || (echo Chua co Python. Tai Python 3.11+ tai https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) roi chay lai. & pause & exit /b 1)
if not exist .venv (
  python -m venv .venv || (echo Loi tao moi truong Python & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || (echo Loi cai thu vien - kiem tra Internet & pause & exit /b 1)
if not exist .env (
  if exist ".env.example" (copy ".env.example" .env >nul) else (if exist ".env - Copy.example" copy ".env - Copy.example" .env >nul)
  echo Da tao file backend\.env - mo file nay dien DEEPSEEK_API_KEY de dung AI doc phieu.
)
python -m pytest -q
echo.
echo Cai dat xong. Chay "chay.bat" de mo phan mem.
pause
