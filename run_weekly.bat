@echo off
REM Weekly digest, run on this PC (home IP, so YouTube doesn't block downloads).
cd /d "%~dp0"
call secrets.bat
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python yt_summariser.py --scan >> output\weekly_log.txt 2>&1
