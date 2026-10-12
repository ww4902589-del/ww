#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Style Atlas - ASCII entry point.

Why this exists
---------------
Windows .cmd / .bat files are read by cmd.exe as ANSI (GBK on a zh-CN box),
NOT as UTF-8. If a launcher contains Chinese characters or references a
Chinese-named script, those bytes turn into garbage and cmd reports
"'xxx' is not recognized as an internal or external command".

So every .cmd / .bat stays 100% ASCII and calls THIS file (ASCII name).
Everything Chinese - the real entry script name, the messages - is handled
here in Python, where wide-character paths are safe.

Usage:
    python atlas_open.py            # = open (same as 风格图鉴.py open)
    python atlas_open.py open
    python atlas_open.py resident
    python atlas_open.py rebuild
    python atlas_open.py status
    python atlas_open.py stop
    python atlas_open.py menu       # interactive menu (no action arg)
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import json

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "http://127.0.0.1:8793"


def find_entry() -> str | None:
    """Locate the CJK-named entry script without hard-coding its bytes."""
    for f in os.listdir(HERE):
        if f.endswith(".py") and f != os.path.basename(__file__) and f.endswith("图鉴.py"):
            return f
    return None


def service_online(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(BASE + "/api/version", timeout=timeout) as r:
            return "version" in json.loads(r.read().decode("utf-8"))
    except Exception:
        return False


def main() -> int:
    entry = find_entry()
    if entry is None:
        print("[ERROR] could not find the entry script (风格图鉴.py) in", HERE)
        return 1

    args = sys.argv[1:]
    action = args[0].lower() if args else "open"
    # "menu" means: run the entry script with NO argument -> interactive menu
    rest = args[1:] if args else []

    if action == "menu":
        forward = []
    else:
        forward = [action] + rest

    # Cold start takes ~30-100s (builds the index). Tell the user, so a
    # frozen-looking console window is not mistaken for a hang.
    if action in ("open", "start", "up") and not service_online():
        print()
        print("  服务未运行，正在启动（首次建索引约 30-100 秒）…")
        print("  这一步只需要等一次，完成后浏览器会自动打开。")
        print()

    t0 = time.time()
    rc = subprocess.call([sys.executable, os.path.join(HERE, entry)] + forward)
    if action in ("open", "start", "up", "menu") and time.time() - t0 > 5:
        print()
        print("  （用时 %.0f 秒）" % (time.time() - t0))
    return rc


if __name__ == "__main__":
    sys.exit(main())
