@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === Actualizando ramas ===
git fetch origin
if errorlevel 1 goto :error

git checkout prod
if errorlevel 1 goto :error

git pull origin prod
if errorlevel 1 goto :error

echo.
echo === Commits que se van a mergear desde preprod a prod ===
git log prod..preprod --oneline
echo.

set /p CONFIRM=Escribe "yes" para hacer merge y push a prod:
if /i not "%CONFIRM%"=="yes" (
    echo Cancelado. No se realizo ningun cambio.
    git checkout preprod
    goto :eof
)

git merge preprod --no-edit
if errorlevel 1 (
    echo ERROR: el merge fallo o tiene conflictos. Resuelvelo manualmente en la rama prod.
    goto :eof
)

git push origin prod
if errorlevel 1 goto :error

git checkout preprod
echo.
echo === Merge y push a prod completados ===
goto :eof

:error
echo ERROR: fallo un paso anterior. Revisa el mensaje de git arriba.
git checkout preprod >nul 2>&1
