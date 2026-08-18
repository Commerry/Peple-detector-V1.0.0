@echo off
rem Factory Box People Counter - start server (frontend served from backend)
cd /d "%~dp0backend"
"%~dp0.venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000
