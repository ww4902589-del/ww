@echo off
REM ============================================================
REM  Style Atlas - single entry shell (ASCII ONLY)
REM
REM  Why ASCII: cmd.exe reads .cmd/.bat as ANSI(GBK). UTF-8
REM  Chinese bytes become garbage and every line errors with
REM  "'xxx' is not recognized as an internal or external command".
REM  All Chinese text lives in the .py files instead.
REM
REM  The real logic is in atlas_open.py (ASCII name), which in
REM  turn calls the CJK-named entry script.
REM
REM  Usage:
REM    double-click        -> open UI (default)
REM    atlas.cmd open      -> alias of this file
REM    atlas.cmd resident  -> run silently in background
REM    atlas.cmd rebuild   -> rebuild index now
REM    atlas.cmd stop      -> stop service
REM    atlas.cmd status    -> show status / log
REM ============================================================
setlocal
cd /d "%~dp0"

set "PY=C:\Users\wyj\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"

set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=open"

"%PY%" "%~dp0atlas_open.py" %ACTION% %2 %3 %4 %5

REM No pause on success: the browser opens on its own. A trailing pause would
REM also mix cmd's GBK prompt into Python's UTF-8 console stream.
endlocal
