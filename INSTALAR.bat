@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title OPERACIONAL - Instalador
cd /d "%~dp0"

echo.
echo  ================================================================
echo   INSTALADOR - Painel e Site OPERACIONAL
echo  ================================================================
echo   Pasta: %CD%
echo.
echo   Responda as tres perguntas abaixo.
echo   Deixe em branco e tecle Enter para manter o que ja esta salvo.
echo.

set "BOT="
set "DB="
set "PIN="
set /p "BOT=  1) Pasta do bot de monitoramento: "
set /p "DB=  2) Pasta do Operacional Database: "
set /p "PIN=  3) PIN de acesso (4 a 12 digitos): "

set "ARGS="
if not "%BOT%"=="" set "ARGS=!ARGS! --bot "%BOT%""
if not "%DB%"=="" set "ARGS=!ARGS! --database "%DB%""
if not "%PIN%"=="" set "ARGS=!ARGS! --pin %PIN%"

rem Prefere uma versao com bibliotecas prontas; o Python mais novo costuma
rem nao ter wheel de pandas/numpy e o pip tentaria compilar do zero.
set "PY="
for %%V in (3.13 3.12 3.11 3.14) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY set "PY=python"

echo.
echo   Usando: !PY!
echo.
!PY! instalar.py !ARGS!

if errorlevel 1 (
  echo.
  echo   A instalacao encontrou problemas. Veja as mensagens acima.
) else (
  echo.
  echo   Pronto. Agora abra o "INICIAR SITE.bat" para subir o site.
)

echo.
pause
