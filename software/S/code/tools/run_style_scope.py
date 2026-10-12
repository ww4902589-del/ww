# -*- coding: utf-8 -*-
"""一键重跑风格能力范围统计 + 可视化报告。

用法：
    python tools/run_style_scope.py
    python tools/run_style_scope.py --out-dir "C:/somewhere"

它会先跑 style_scope_report.py 产出 style_scope_deep.json，
再跑 build_style_report.py 把 JSON 渲染成单文件 HTML。
"""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(script, out_dir):
    fp = os.path.join(HERE, script)
    if not os.path.exists(fp):
        sys.exit(f"缺少脚本：{fp}")
    print(f"\n>>> {script}")
    r = subprocess.run([PY, fp, "--out-dir", out_dir], cwd=HERE)
    if r.returncode != 0:
        sys.exit(f"{script} 失败（exit {r.returncode}）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=HERE)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    run("style_scope_report.py", a.out_dir)
    run("build_style_report.py", a.out_dir)

    print("\n完成。产出：")
    for f in ("style_scope_deep.json", "风格能力范围统计报告.html"):
        p = os.path.join(a.out_dir, f)
        if os.path.exists(p):
            print(f"  {p}  ({os.path.getsize(p):,} bytes)")


if __name__ == "__main__":
    main()
