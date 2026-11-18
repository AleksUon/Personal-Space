@echo off
cd /d "%~dp0"
py -m pip install -r requirements.txt
if errorlevel 1 (
  echo Не удалось установить зависимости. Проверьте Python и подключение к Интернету.
  pause
  exit /b 1
)
py main.py
if errorlevel 1 pause
