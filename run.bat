@echo off
rem Starts Hush on Windows. First run creates the virtual environment and installs dependencies.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setting up Hush for the first time...
  python -m venv .venv || goto :nopython
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :eof
)
start "" ".venv\Scripts\pythonw.exe" -m hush
goto :eof

:nopython
echo Python 3.12 is required: winget install Python.Python.3.12
pause
