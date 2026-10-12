@echo off
REM ============================================================
REM  Style Atlas - menu launcher  (ASCII ONLY)
REM
REM  Why ASCII: cmd.exe reads .cmd/.bat as ANSI(GBK). UTF-8
REM  Chinese bytes become garbage and every line errors with
REM  "'xxx' is not recognized as an internal or external command".
REM  All Chinese text lives in the .py files instead.
REM
REM  Double-click = show the interactive menu (items 1-6).
REM  The menu itself lives in the CJK-named entry .py, reached
REM  through atlas_open.py so this file stays pure ASCII.
REM
REM  Direct actions (no menu):
REM    atlas.cmd open       open the UI
REM    atlas.cmd resident   run in background
REM    atlas.cmd rebuild    rebuild index now
REM    atlas.cmd stop       stop the service
REM    atlas.cmd status     show status / log
REM ============================================================
setlocal
cd /d "%~dp0"

set "PY=C:\Users\wyj\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"

REM No argument -> interactive menu. With argument -> passthrough.
REM No pause here: the menu has its own "press Enter to return", and a
REM trailing pause mixes cmd's GBK prompt into Python's UTF-8 stream.
if "%~1"=="" (
  "%PY%" "%~dp0atlas_open.py" menu
) else (
  "%PY%" "%~dp0atlas_open.py" %*
)

endlocal
