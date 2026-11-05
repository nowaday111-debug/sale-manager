@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === DUA DU AN SALE-MANAGER LEN GITHUB ===
where git >nul 2>nul || (echo Chua cai Git. Tai tai https://git-scm.com/download/win roi chay lai. & pause & exit /b 1)
if not exist .git git init -b main
git config user.name >nul 2>nul || git config user.name "DEV-FUN"
git config user.email >nul 2>nul || git config user.email "nowaday111@gmail.com"
git remote get-url origin >nul 2>nul || git remote add origin https://github.com/nowaday111-debug/sale-manager.git
git add -A
git commit -m "Cap nhat Sale-manager %date% %time%" || echo (Khong co thay doi moi de commit)
echo.
echo Neu hien cua so dang nhap GitHub: chon "Sign in with your browser" va bam Authorize.
git push -u origin main
if errorlevel 1 (echo. & echo DAY LEN THAT BAI - xem thong bao loi o tren. & pause & exit /b 1)
echo.
echo XONG! Xem tai: https://github.com/nowaday111-debug/sale-manager
start "" https://github.com/nowaday111-debug/sale-manager
pause
