# PowerShell script to start the SAP BRIM Knowledge Assistant backend
$ErrorActionPreference = "Stop"

# Set current directory to project root
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

# Set PYTHONPATH to use venv packages and project root
$env:PYTHONPATH = "$ProjectRoot\.venv\Lib\site-packages;$ProjectRoot"

# Locate signed Python 3.11 binary
$PythonExe = "C:\Users\Subham Patnaik\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "$ProjectRoot\.venv\Scripts\python.exe"
}

Write-Host "Starting SAP BRIM Knowledge Assistant backend on http://localhost:8000..." -ForegroundColor Cyan
& $PythonExe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
