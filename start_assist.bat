@echo off
setlocal
rem Start the writing helper (assist/): select text anywhere, press Ctrl+Alt+W.
rem Uses Anaconda's pythonw.exe in the user's profile, else pythonw.exe on PATH.
cd /d "%~dp0"
set "DAOHUI_PYW=%USERPROFILE%\anaconda3\pythonw.exe"
if not exist "%DAOHUI_PYW%" set "DAOHUI_PYW=pythonw.exe"
start "" "%DAOHUI_PYW%" -m assist
