@echo off
setlocal
cd /d "%~dp0"

git checkout preprod
if errorlevel 1 goto :error

echo.
git status
echo.

set "MSG=%*"
if "%MSG%"=="" (
    set /p MSG=Mensaje de commit:
)
if "%MSG%"=="" (
    echo No se proporciono mensaje de commit. Cancelado.
    goto :eof
)

git add -A
git commit -m "%MSG%"
if errorlevel 1 (
    echo No hay cambios para commitear, o el commit fallo.
    goto :eof
)

git push
if errorlevel 1 goto :error

echo.
echo === Commit y push a preprod completados ===
goto :eof

:error
echo ERROR: fallo un paso anterior. Revisa el mensaje de git arriba.
