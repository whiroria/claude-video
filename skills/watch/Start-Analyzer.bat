@echo off
cd /d "%~dp0"
python -X utf8 scripts\desktop.py
if errorlevel 1 (
 echo Application could not start. Check that Python with Tcl/Tk is installed.
 pause
)
