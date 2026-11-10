@echo off
REM Channel archiver, run on this PC. Resumes where it left off each time.
cd /d "%~dp0"
call secrets.bat
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python channel_archive.py --channel "@akbarfaizaluncensored" --cap 100
git add archive/
git commit -m "Archive batch (local)"
git pull --rebase
git push
