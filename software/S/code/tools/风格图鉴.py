#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
风格图鉴 · 一体化启动器
============================================================
把原来散落的多个入口收拢成一个程序，双击即可用：

    风格图鉴.py            ← 唯一的入口（菜单式）
    风格图鉴.cmd           ← 双击这个（Windows 壳，调用上面的 .py）

菜单项：
    1  打开图鉴界面        （没在跑就自动拉起，已在跑就直接开浏览器）
    2  后台常驻            （不弹浏览器，静默跑着，供开机自启用）
    3  停止服务
    4  立即重建索引        （ComfyUI 出新图后手动刷一下）
    5  查看运行状态 / 日志
    6  打开数据目录        （tools / 图片根 / 标记文件）
    0  退出

设计要点
------------------------------------------------------------
* **身份校验**：判断"服务在不在"不是看端口通不通，而是请求
  `/api/version` 能否返回预期 JSON。别的程序占着 8793 时端口照样"通"，
  但它返回 502/404，页面图全裂 —— 这是「图片加载不出来」的根因。
* **端口回收**：确认端口被外来程序占用时，先结束它再启动自家服务。
* **幂等**：任何操作重复执行都安全（已在跑就不再起、没在跑就跳过）。
* **不依赖第三方库**：只用标准库，任何 Python 3.8+ 都能跑。
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8793
BASE = f"http://127.0.0.1:{PORT}"
LOG = os.path.join(HERE, "_atlas_autostart.log")
SERVER = os.path.join(HERE, "atlas_server.py")

# 优先用项目自带的 python（装在哪台机器都能找到同一个），找不到就退回当前解释器
PY_CANDIDATES = [
    r"C:\Users\wyj\.workbuddy\binaries\python\versions\3.13.12\python.exe",
    sys.executable,
]


def PY():
    for p in PY_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return sys.executable


# ---------------------------------------------------------------- 服务探测

def get_version(timeout=3):
    """服务在跑 → 返回 dict；不在跑 / 不是自家服务 → None。

    只请求自家特有的接口，能解析出预期字段才承认。这是关键：
    仅凭 socket 连得上会把"端口被别的程序占着"误判成"已在线"。
    """
    try:
        with urllib.request.urlopen(f"{BASE}/api/version", timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        return d if isinstance(d, dict) and "version" in d else None
    except Exception:
        return None


def port_pids():
    """返回占用 8793 且处于 LISTENING 的进程 PID 列表。"""
    pids = []
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             encoding="utf-8", errors="ignore").stdout or ""
    except Exception:
        return pids
    for line in out.splitlines():
        if f":{PORT}" in line and "LISTENING" in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.append(parts[-1])
    return pids


def kill(pid):
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def free_port(verbose=True):
    """腾出 8793：只有确认不是自家服务时才动手。"""
    if get_version():
        return False                       # 自家服务在跑，别乱杀
    for pid in set(port_pids()):
        if verbose:
            print(f"  [占用] 8793 被 PID={pid} 占用但不是图鉴服务，正在腾出端口 …")
        kill(pid)
    time.sleep(1)
    return True


# ---------------------------------------------------------------- 服务起停

def wait_ready(seconds=180, quiet=False):
    """轮询 /api/version 直到就绪。返回 dict 或 None。"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        d = get_version(timeout=2)
        if d:
            return d
        if not quiet and int(time.time() - t0) % 15 == 0 and time.time() - t0 > 1:
            print(f"  … 已等待 {int(time.time()-t0)}s（索引 17000+ 张图，首次较慢）")
        time.sleep(1)
    return None


def start_server(watch=15):
    """后台拉起服务，关掉本窗口也不会停。

    关键：用 `cmd /c start /b` 做一级 shell 中转来脱离父进程树。
    直接 subprocess.Popen 出来的子进程会**跟着父进程一起被回收**
    （实测：日志显示已建好索引 v1，但父进程一退服务就没了）。
    `start` 是 cmd 的内建命令，由它拉起的进程挂在 explorer/session 上，
    父进程退出后继续活着 —— 这也是原 bat 一直好用的原因。
    """
    if get_version():
        return get_version()
    free_port()
    print("  [启动中] 正在构建索引，首次约 30-100 秒 …")

    logf = open(LOG, "ab")
    if os.name == "nt":
        # cmd /c start "" /b <py> <script> ...   （"" 是窗口标题占位，必须留）
        cmd = (f'start "" /b "{PY()}" "{SERVER}" '
               f'--port {PORT} --watch-interval {watch}')
        subprocess.Popen(cmd, cwd=HERE, shell=True,
                         stdout=logf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL)
    else:
        subprocess.Popen([PY(), SERVER, "--port", str(PORT),
                          "--watch-interval", str(watch)],
                         cwd=HERE, stdout=logf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    logf.close()
    return wait_ready()


def stop_server():
    d = get_version()
    if not d:
        # 端口上可能有外来程序，但停止操作只针对自家服务
        print("  服务本来就没在运行。")
        return True
    pids = port_pids()
    if not pids:
        print("  !! 服务无响应，且找不到监听进程。")
        return False
    for pid in set(pids):
        print(f"  结束进程 PID={pid}")
        kill(pid)
    time.sleep(1.5)
    ok = get_version() is None
    print("  已停止。" if ok else "  !! 仍在运行，请到任务管理器结束 python.exe")
    return ok


def rebuild():
    d = get_version()
    if not d:
        print("  服务没在跑 —— 正在先启动它 …")
        d = start_server()
        if not d:
            print("  启动失败，请看日志。")
            return
    print(f"  正在重建索引（当前 v{d.get('version')} · {d.get('images')} 张）…")
    try:
        req = urllib.request.Request(f"{BASE}/api/rebuild", data=b"{}",
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=300) as r:
            j = json.loads(r.read().decode("utf-8"))
        if j.get("ok"):
            print(f"  完成：v{j.get('version')} · 耗时 {j.get('seconds')}s")
        else:
            print(f"  重建失败：{j}")
    except Exception as e:
        print(f"  重建请求失败：{e}")


# ---------------------------------------------------------------- 菜单动作

# ---------------------------------------------------------------- 单实例归一

def tab_alive():
    """图鉴页面是否还开着（ALIVE_WINDOW 秒内有过页面心跳）。

    页面每 5s POST /api/alive 报活；服务端记最近心跳时间。
    入口 open 前查它：窗口活着就不重复开标签，只发 focus 通知。
    """
    try:
        with urllib.request.urlopen(f"{BASE}/api/focus", timeout=3) as r:
            j = json.loads(r.read().decode("utf-8"))
        return bool(j.get("alive"))
    except Exception:
        return False


def notify_focus():
    """通知已有页面自聚焦（focus seq +1，页面心跳发现变化就置前）。"""
    try:
        req = urllib.request.Request(f"{BASE}/api/focus", data=b"{}",
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass


def act_open():
    d = get_version()
    if d:
        print(f"  [已在线] 服务正在运行（v{d.get('version')} · {d.get('images')} 张图）")
        # 每次进入都刷新一次：重建索引 → 顺带把桌面静态副本覆盖成最新。
        # 需求原话「每一次进入风格图鉴后出现，替换原文件」——
        # 服务常驻时光开浏览器不会重建，桌面那份就停在旧版，所以这里主动踢一次。
        # 走 HTTP /api/rebuild，服务端 rebuild() 里的 _sync_desktop_copy()
        # 会负责覆盖 ~/Desktop/风格图鉴.html。失败也不拦着打开浏览器。
        refresh_desktop_copy()
        # 单实例归一：窗口活着 → 不开新标签，通知已有页面置前后直接返回。
        # 这样无论双击多少次启动器，永远只有唯一一个图鉴窗口。
        if tab_alive():
            notify_focus()
            print("  [归一] 图鉴窗口已开着 —— 已通知它置前，不再重复开新窗口。")
            print(f"  窗口地址：{BASE}/   （若浏览器没自动切过去，点任务栏的浏览器图标）")
            print()
            print("  用法提示：")
            print("    - 图库：左侧点标签叠加筛选（如 动漫服装 + 甜彩线描）")
            print("    - 标星：点图片左上角 ☆ 标记；看图时按 M 键更快")
            print("    - 标记：与图库/统计并列的标签页，可管控、导出、反推配方")
            print("    - 刷新：ComfyUI 出新图会自动进来；也可点顶上「↻ 刷新」立即更新")
            return
    else:
        d = start_server()
        if not d:
            print("  [超时] 启动失败，请看日志（菜单选 5 可查看）。")
            return
        print(f"  [就绪] 服务已启动（v{d.get('version')} · {d.get('images')} 张图）")
    webbrowser.open(BASE + "/")
    print(f"  已在浏览器打开：{BASE}/")
    print()
    print("  用法提示：")
    print("    - 图库：左侧点标签叠加筛选（如 动漫服装 + 甜彩线描）")
    print("    - 标星：点图片左上角 ☆ 标记；看图时按 M 键更快")
    print("    - 标记：与图库/统计并列的标签页，可管控、导出、反推配方")
    print("    - 刷新：ComfyUI 出新图会自动进来；也可点顶上「↻ 刷新」立即更新")


def refresh_desktop_copy():
    """把桌面静态副本刷成最新（覆盖原文件）。失败只提示，不阻断打开。

    走轻量端点 /api/sync_desktop —— 只复制文件、不重建索引，<1 秒。
    （早先想用 /api/rebuild，但那要 20+ 秒，每次进入都卡太难受。）
    副本内容 = 服务端当前已渲染的 HTML，所以不会产生版本漂移。
    """
    try:
        req = urllib.request.Request(f"{BASE}/api/sync_desktop", data=b"{}",
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode("utf-8"))
        if j.get("ok"):
            print(f"  [副本已刷新] 桌面「风格图鉴.html」{j.get('bytes', 0):,} 字节")
        else:
            print(f"  [副本未刷新] {j.get('error') or j.get('skipped') or j}")
    except Exception as e:
        print(f"  [副本刷新跳过] {e}")


def act_resident():
    d = get_version()
    if d:
        print(f"  已在运行（v{d.get('version')} · {d.get('images')} 张图），跳过。")
        return
    d = start_server()
    if d:
        print(f"  后台常驻已启动（v{d.get('version')} · {d.get('images')} 张图）。")
        print(f"  界面地址：{BASE}/   （随时用菜单 1 打开）")
    else:
        print("  启动失败，请看日志。")


def act_status():
    d = get_version()
    print("  ── 运行状态 ──")
    if d:
        print(f"  服务：在线")
        print(f"  版本：v{d.get('version')}")
        print(f"  图片：{d.get('images')} 张")
        print(f"  最近重建：v{d.get('version')} · 耗时 {d.get('last_ms', 0)/1000:.1f}s")
        print(f"  重建中：{'是' if d.get('building') else '否'}")
        if d.get("error"):
            print(f"  !! 最近错误：{d.get('error')}")
    else:
        print("  服务：离线")
        pids = port_pids()
        if pids:
            print(f"  !! 8793 被非图鉴程序占用，PID={','.join(pids)}")
    print()
    print("  ── 关键路径 ──")
    for label, p in (("程序目录", HERE),
                     ("入口脚本", os.path.join(HERE, "风格图鉴.py")),
                     ("服务脚本", SERVER),
                     ("页面文件", os.path.join(HERE, "风格图鉴.html")),
                     ("标记数据", os.path.join(HERE, "_marks.json")),
                     ("运行日志", LOG)):
        print(f"  {'√' if os.path.exists(p) else '×'} {label}\t{p}")
    print()
    print("  ── 日志末尾 15 行 ──")
    if os.path.isfile(LOG):
        try:
            with open(LOG, encoding="utf-8", errors="replace") as f:
                for ln in f.read().splitlines()[-15:]:
                    print("   ", ln)
        except Exception as e:
            print("    读取失败:", e)
    else:
        print("    （还没有日志）")


def act_open_dir():
    d = get_version()
    targets = []
    if d and d.get("error"):
        targets.append(HERE)
    targets.append(HERE)
    for t in dict.fromkeys(targets):
        try:
            if os.name == "nt":
                os.startfile(t)          # noqa: S606
            else:
                webbrowser.open("file://" + t)
            print(f"  已打开：{t}")
        except Exception as e:
            print(f"  打开失败 {t}: {e}")


MENU = [
    ("1", "打开图鉴界面", act_open),
    ("2", "后台常驻（静默运行，供开机自启用）", act_resident),
    ("3", "停止服务", stop_server),
    ("4", "立即重建索引", rebuild),
    ("5", "查看运行状态 / 日志", act_status),
    ("6", "打开程序目录", act_open_dir),
    ("0", "退出", None),
]


def banner():
    d = get_version()
    state = (f"● 运行中  v{d.get('version')} · {d.get('images')} 张图"
             if d else "○ 未运行")
    print()
    print("  ============================================================")
    print("     风格图鉴  -  多标签分类 / 叠加精确定位")
    print("                  ★ 标记与配方反推   ↻ 新图自动进入")
    print("  ============================================================")
    print(f"     状态：{state}")
    print(f"     地址：{BASE}/")
    print("  ============================================================")
    print()


def main(argv):
    args = [a.lower() for a in argv[1:]]

    # 支持命令行直调，便于挂快捷方式/自动化：
    #   python 风格图鉴.py open | resident | stop | rebuild | status
    quick = {"open": act_open, "start": act_open, "up": act_open,
             "resident": act_resident, "daemon": act_resident,
             "stop": lambda: stop_server(), "down": lambda: stop_server(),
             "rebuild": rebuild, "refresh": rebuild,
             "status": act_status, "st": act_status}
    for a in args:
        if a in quick:
            banner()
            quick[a]()
            return 0

    banner()
    if not get_version():
        # 双击进来且服务没起 —— 默认直接开门见山
        pass

    while True:
        print("  ── 请选择 ──")
        for key, label, _ in MENU:
            print(f"   {key}  {label}")
        print()
        try:
            c = input("  输入编号后回车：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if c in ("0", "q", "quit", "exit", ""):
            print("  再见。")
            return 0
        hit = next((f for k, _, f in MENU if k == c), None)
        if not hit:
            print("  ?  没有这个选项，请重新输入。")
            print()
            continue
        print()
        try:
            hit()
        except Exception as e:
            print(f"  !! 出错了：{e}")
        print()
        input("  按回车返回菜单 …")
        print()
        banner()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
