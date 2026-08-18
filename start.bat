@echo off
rem People Counter - start server (backend serves built frontend at http://localhost:8000)
cd /d "%~dp0backend"
"%~dp0.venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
