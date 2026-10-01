@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title BPM Renamer - Gerar instalador

echo ============================================================
echo   BPM Renamer - gerador do instalador para Windows 10/11
echo ============================================================
echo.

rem ---------- 1. Python ----------
set "PYCMD="
py -3.11 --version >nul 2>&1
if not errorlevel 1 set "PYCMD=py -3.11"
if not defined PYCMD (
    py -3.12 --version >nul 2>&1
    if not errorlevel 1 set "PYCMD=py -3.12"
)
if not defined PYCMD (
    py -3.10 --version >nul 2>&1
    if not errorlevel 1 set "PYCMD=py -3.10"
)
if not defined PYCMD (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PYCMD=py -3"
)
if not defined PYCMD goto sem_python

echo [1/5] Python encontrado:
%PYCMD% --version
echo.

rem ---------- 2. Ambiente virtual + bibliotecas ----------
echo [2/5] Preparando o ambiente e baixando as bibliotecas.
echo       Na primeira vez isso pode levar varios minutos. Aguarde...
if not exist ".venv\Scripts\python.exe" (
    %PYCMD% -m venv .venv
    if errorlevel 1 goto erro
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 goto erro
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto erro
echo.

rem ---------- 3. Gerar o programa ----------
echo [3/5] Gerando o programa com todas as bibliotecas embutidas...
python -m PyInstaller --noconfirm --clean bpm_renamer.spec
if errorlevel 1 goto erro
echo.

rem ---------- 4. Autoteste do programa gerado ----------
echo [4/5] Testando o programa gerado (deteccao de BPM e de tom)...
if exist "%TEMP%\bpmrenamer_selftest.log" del "%TEMP%\bpmrenamer_selftest.log"
start /wait "" "dist\BPMRenamer\BPMRenamer.exe" --selftest
set "RC=%errorlevel%"
if exist "%TEMP%\bpmrenamer_selftest.log" type "%TEMP%\bpmrenamer_selftest.log"
echo.
if not "%RC%"=="0" goto erro_teste

rem ---------- 5. Instalador (Inno Setup) ----------
echo [5/5] Criando o instalador...
call :achar_iscc
if not defined ISCC (
    echo Inno Setup nao encontrado. Tentando instalar com o winget...
    winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements
    call :achar_iscc
)
if not defined ISCC goto sem_inno

"%ISCC%" installer.iss
if errorlevel 1 goto erro

echo.
echo ============================================================
echo   PRONTO! O instalador esta na pasta:
echo   %~dp0installer_output
echo ============================================================
explorer "%~dp0installer_output"
pause
exit /b 0

:achar_iscc
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
exit /b 0

:sem_python
echo.
echo Python 3 nao foi encontrado.
echo Instale o Python 3.11 (64 bits) em https://www.python.org/downloads/windows/
echo marcando a opcao "Add python.exe to PATH" e o "py launcher", e rode este arquivo de novo.
pause
exit /b 1

:sem_inno
echo.
echo Nao foi possivel localizar/instalar o Inno Setup automaticamente.
echo Baixe e instale o Inno Setup 6 (gratuito) em https://jrsoftware.org/isdl.php
echo e rode este arquivo de novo. O programa ja foi gerado em: dist\BPMRenamer
pause
exit /b 1

:erro_teste
echo.
echo O autoteste do programa gerado FALHOU (veja o log acima).
echo O instalador NAO foi criado para evitar distribuir uma versao com problema.
pause
exit /b 1

:erro
echo.
echo Ocorreu um erro. Leia as mensagens acima para ver o que falhou.
pause
exit /b 1
