@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py"
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD goto no_python
if not exist .venv\Scripts\python.exe (
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -c "import PIL" >nul 2>nul
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe app.py
if errorlevel 1 goto failed
exit /b 0
:no_python
echo لم يتم العثور على Python في Windows.
echo ثبّت Python من https://www.python.org/downloads/windows/ ثم فعّل الخيار Add Python to PATH.
echo بعد التثبيت أغلق هذه النافذة وافتح run.bat مجددًا.
pause
exit /b 1
:failed
echo.
echo حدث خطأ أثناء إعداد التطبيق أو تشغيله. تأكد من تثبيت Python 3.10 أو أحدث.
pause
exit /b 1
