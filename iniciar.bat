@echo off
rem Inicia o Baixador no seu computador (Windows).
rem Precisa do Python 3.9 ou mais novo instalado (python.org, marque "Add Python to PATH").
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python nao encontrado. Instale em https://www.python.org/downloads/ e marque "Add Python to PATH".
  pause
  exit /b 1
)

if not exist .venv (
  echo Criando ambiente ^(so na primeira vez^)...
  python -m venv .venv
)
call .venv\Scripts\activate

echo Instalando/atualizando programas...
python -m pip install -q --upgrade pip
python -m pip install -q --upgrade -r requirements.txt

echo.
python app.py
pause
