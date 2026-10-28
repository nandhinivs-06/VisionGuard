@echo off
REM VisionGuard web dashboard (HTML/CSS/JS + FastAPI)
cd /d "%~dp0"
start "" http://localhost:8080
python -m webapp.server --port 8080
