@echo off
chcp 65001 >nul
title OPERACIONAL - Site do Painel
cd /d "%~dp0"

rem Usa o mesmo Python que o instalador validou. Numa maquina com varias
rem versoes, "python" no PATH pode ser outra - a que nao tem as bibliotecas.
set "PY=python"
if exist "config\python.txt" set /p PY=<config\python.txt
if not exist "%PY%" if not "%PY%"=="python" set "PY=python"

echo.
echo  Subindo o site do painel OPERACIONAL...
echo  Python: %PY%
echo  Deixe esta janela aberta enquanto o site estiver no ar.
echo  Para desligar, feche a janela ou pressione Ctrl+C.
echo.

"%PY%" iniciar_site.py %*

if errorlevel 1 (
  echo.
  echo  O site nao subiu. Se a mensagem citar modulo faltando,
  echo  rode o INSTALAR.bat antes.
)

echo.
echo  O site foi encerrado.
pause
