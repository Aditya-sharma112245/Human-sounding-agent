@echo off
title WhatsApp Agent Launcher
echo ====================================================
echo Starting Human-Sounding WhatsApp Agent...
echo ====================================================

:: Start the FastAPI server in a new command window
echo Starting application server on port 8000...
start "WhatsApp Agent Backend" cmd /k "venv\Scripts\python main.py"

:: Start the Ngrok tunnel in a new command window
echo Starting Ngrok tunnel (trespass-staple-jailbreak.ngrok-free.dev)...
start "Ngrok Tunnel" cmd /k "ngrok.exe http 8000 --domain=trespass-staple-jailbreak.ngrok-free.dev"

echo ====================================================
echo Both processes have started in separate windows!
echo Keep those windows open (or minimized) to run.
echo Close them to stop the agent.
echo ====================================================
timeout /t 5
