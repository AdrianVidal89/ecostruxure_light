@echo off
cd /d %~dp0

echo Activando entorno virtual...
call .venv\Scripts\activate.bat

echo Lanzando servidor Django...
python manage.py runserver

pause