@echo off
rem Starts Jarvis: orb + panel UI, "Hey Jarvis" wake word, voice, desktop control.
cd /d "%~dp0app"
start "" "%~dp0.venv\Scripts\pythonw.exe" main.py
