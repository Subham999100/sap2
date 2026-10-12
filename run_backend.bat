@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0\.venv\Lib\site-packages;%~dp0"
set "PYTHON_EXE=C:\Users\Subham Patnaik\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe"

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=%~dp0\.venv\Scripts\python.exe"
)

echo Starting SAP BRIM Knowledge Assistant backend on http://localhost:8000...
"%PYTHON_EXE%" -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
pause
