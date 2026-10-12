# -*- coding: utf-8 -*-
"""把「风格图鉴」内嵌进 S 自己的进程里。

为什么是内嵌，而不是拉起一个子进程
----------------------------------
需求原话是「它们组合为一个完整的程序」。之前的形态只在界面层组合：
S 启动后探测 8793，探测不到就让用户「双击桌面的『风格图鉴-打开.cmd』」。
两个程序、两次启动，而且那个 .cmd 依赖本机装好的 Python 解释器，
换台机器就断 —— 这不是组合，是两个恰好长得挨着的工具。

S 打成单文件 exe 之后没有解释器可以指（``sys.executable`` 就是 S.exe 自己），
所以子进程方案要么再随包发一份 Python，要么让 S.exe 带特殊参数再解包一次自己
——后者每次多一份 _MEIPASS，慢且占地方。而图鉴服务本身就是一个标准库
``ThreadingHTTPServer``，直接在同进程起一条线程最干净：

* 一个 exe、一次双击，两个模块都在；
* 生命周期天然一致 —— S 退出，8793 跟着消失，不会留下占着端口的僵尸进程；
* 前端的探测逻辑不用改，8793 仍在原处，iframe 照旧嵌入。

外部如果已经开着图鉴（用户手动跑过启动器 / 命令行调试），这里不抢绑：
8793 已被占用就判定为「复用外部实例」，S 照常工作。

失败处理原则：图鉴起不来**绝不能**影响生图工作台。
所以 start() 不抛异常，出错就把原因写进日志与返回值，S 那边照常 serve。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import time
import urllib.error
import urllib.request

#: 图鉴服务的默认端口。与 app.js 里的探测地址必须一致。
ATLAS_PORT = 8793

#: 随程序分发的只读系统评级；用户收藏始终来自持久化数据目录。
_SEED_FILES = ("_grades_system.json",)

#: 单次探测的等待上限。本机回环，正常情况下是毫秒级。
_PROBE_TIMEOUT = 0.7


def tools_dir() -> pathlib.Path:
    """源码树里的图鉴目录。打包后不存在（代码在 pyz 里），返回一个不存在的路径。"""
    return pathlib.Path(__file__).resolve().parent.parent / "tools"


def ensure_importable() -> None:
    """让 ``import atlas_server`` 在源码运行时也能找到 —— 打包后它已在 pyz 里，
    这个插入是空操作（目录不存在）。"""
    d = tools_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))


def probe(port: int = ATLAS_PORT, timeout: float = _PROBE_TIMEOUT) -> dict | None:
    """8793 上是不是已经有一个图鉴服务在应答。

    认的是 ``/api/version`` 返回里带 ``version`` 字段 —— 只看端口通不通不够：
    端口可能被别的程序占着，那种情况必须报错而不是当成「图鉴已在运行」。
    """
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f"http://127.0.0.1:{port}/api/version", timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8"))
        return j if isinstance(j, dict) and "version" in j else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _seed_user_data(data_dir: str) -> list[str]:
    """首次安装使用空模板与系统评级；升级保留已有 JSON / SQLite 数据。"""
    seeded: list[str] = []
    meipass = getattr(sys, "_MEIPASS", "")
    if not meipass:
        return seeded                      # 源码运行：数据本来就在原地，无需播种
    # User collections belong to persistent AppData, never a stale bundled copy.
    # New installs receive empty templates; upgrades keep the existing database/JSON.
    template = os.path.join(meipass, 'atlas_initial_data.json')
    if os.path.isfile(template):
        with open(template, encoding='utf-8') as f:
            initial = json.load(f)
        for name, value in initial.items():
            dst = os.path.join(data_dir, name)
            if not os.path.exists(dst) and not os.path.exists(os.path.join(data_dir, 'resources.sqlite3')):
                with open(dst, 'x', encoding='utf-8') as f:
                    json.dump(value, f, ensure_ascii=False)
                seeded.append(name)
    for name in _SEED_FILES:
        dst = os.path.join(data_dir, name)
        if os.path.exists(dst):
            continue
        src = os.path.join(meipass, name)
        if os.path.isfile(src):
            try:
                shutil.copyfile(src, dst)
                seeded.append(name)
            except OSError:
                pass
    return seeded


class _Log:
    """console=False 的 exe 里 print 是黑洞，所以同时落一份文件。

    图鉴起不来的时候，用户能说的只有「打不开」；这一行日志是唯一的证据。
    """

    MAX_BYTES = 256 * 1024

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "_atlas_host.log")

    def __call__(self, msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        try:
            print(line, flush=True)
        except Exception:
            pass
        try:
            if os.path.isfile(self.path) and os.path.getsize(self.path) > self.MAX_BYTES:
                os.replace(self.path, self.path + ".1")
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def start(port: int = ATLAS_PORT, bootstrap: str = "background",
          watch_interval: float = 20.0) -> dict:
    """在同进程里把图鉴服务跑起来。**不会抛异常。**

    返回 ``{"ok", "embedded", "port", "reason", "seeded", ...}``：
    ``embedded=True`` 表示是本进程起的；``False`` 表示复用了外部已有实例。
    """
    existing = probe(port)
    if existing is not None:
        return {"ok": True, "embedded": False, "port": port,
                "reason": "已有图鉴服务在运行，直接复用",
                "images": existing.get("images"), "version": existing.get("version")}

    try:
        ensure_importable()
        import atlas_server as S
    except Exception as exc:                       # pragma: no cover - 打包缺件才会走到
        return {"ok": False, "embedded": False, "port": port,
                "reason": f"图鉴模块导入失败：{exc!r}"}

    try:
        os.makedirs(S.DATA_DIR, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "embedded": False, "port": port,
                "reason": f"数据目录不可写（{S.DATA_DIR}）：{exc}"}

    log = _Log(S.DATA_DIR)
    seeded = _seed_user_data(S.DATA_DIR)
    log(f"[图鉴] 内嵌启动 · 端口 {port} · 数据目录 {S.DATA_DIR}"
        + (f" · 首次铺入 {', '.join(seeded)}" if seeded else ""))

    try:
        srv, _http = S.serve(port=port, bootstrap=bootstrap,
                             watch_interval=watch_interval, log=log)
    except OSError as exc:
        # 最常见的两种：端口被非图鉴程序占着，或数据目录被占。
        log(f"[图鉴] 启动失败：{exc}")
        return {"ok": False, "embedded": False, "port": port,
                "reason": f"端口 {port} 无法绑定：{exc}", "seeded": seeded,
                "log": log.path}
    except Exception as exc:                       # pragma: no cover - 兜底
        log(f"[图鉴] 启动异常：{exc!r}")
        return {"ok": False, "embedded": False, "port": port,
                "reason": f"启动异常：{exc!r}", "seeded": seeded, "log": log.path}

    log("[图鉴] 服务线程已就绪" + ("（索引后台构建中）" if bootstrap == "background" else ""))
    return {"ok": True, "embedded": True, "port": port, "reason": "",
            "seeded": seeded, "server": srv, "data_dir": S.DATA_DIR,
            "log": log.path}
