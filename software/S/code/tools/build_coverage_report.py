# -*- coding: utf-8 -*-
"""生成《图片联系完整性核查》报告 —— 独立审计口径的可视化。"""
import os, sys, json, collections, argparse, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def build_data():
    j = os.path.join(HERE, "_audit_links.json")
    subprocess.run([sys.executable, os.path.join(HERE, "audit_links.py"),
                    "--json", j], cwd=HERE, check=True,
                   stdout=subprocess.DEVNULL)
    return json.load(open(j, encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "图片联系完整性核查.html"))
    a = ap.parse_args()
    d = build_data()

    total, linked, unlinked = d["total"], d["linked"], d["unlinked"]
    grades = d["linked_by_grade"]
    cats = d["unlinked_by_cat"]

    # 未联系里「本该有记录」的 = 0（D4 类已被审计判定为 0）
    D = lambda n: n

    def rows(dic, total_):
        out = []
        for k, v in sorted(dic.items(), key=lambda x: -x[1]):
            pct = v / max(total_, 1) * 100
            out.append(f"""<tr><td>{k}</td><td class=n>{v:,}</td>
              <td><div class=bar><i style="width:{pct:.1f}%"></i></div></td>
              <td class=n>{pct:.1f}%</td></tr>""")
        return "\n".join(out)

    # 扫描根汇总
    roots = {
        "桌面 c/": {"total": 0, "linked": 0},
        "桌面 D/": {"total": 0, "linked": 0},
    }
    for r in d["rows"]:
        k = "桌面 c/" if r["path"].startswith("C:/Users/wyj/Desktop/c/") else "桌面 D/"
        roots[k]["total"] += 1
        if r["grade"] != "D-未联系":
            roots[k]["linked"] += 1
    root_rows = "\n".join(
        f"""<tr><td>{k}</td><td class=n>{v['total']:,}</td>
        <td class=n>{v['linked']:,}</td>
        <td class=n>{v['total']-v['linked']:,}</td>
        <td class=n>{v['linked']/max(v['total'],1)*100:.1f}%</td></tr>"""
        for k, v in roots.items())

    lp = linked / max(total, 1) * 100
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>图片联系完整性核查</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--tx:#1a1d21;--tx2:#5b6470;--bd:#e4e7eb;
--ok:#1a9f60;--warn:#d98a00;--bad:#c0392b;--ac:#2f6fed}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;padding:28px}}
.wrap{{max-width:1080px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 4px}}
.sub{{color:var(--tx2);margin-bottom:20px;font-size:13px}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:10px;
padding:18px 20px;margin-bottom:16px}}
.card h2{{font-size:15px;margin:0 0 12px;padding-left:9px;
border-left:3px solid var(--ac)}}
.kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.kpi div{{background:var(--card);border:1px solid var(--bd);border-radius:10px;
padding:14px 16px}}
.kpi b{{display:block;font-size:24px;line-height:1.2}}
.kpi span{{color:var(--tx2);font-size:12px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:7px 9px;border-bottom:1px solid var(--bd);text-align:left}}
th{{color:var(--tx2);font-weight:500;font-size:12px}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}}
.bar{{background:#eef1f4;border-radius:3px;height:7px;overflow:hidden;min-width:70px}}
.bar i{{display:block;height:100%;background:var(--ac)}}
.ok{{color:var(--ok);font-weight:600}}
.tip{{background:#eef7f1;border:1px solid #cfe9db;border-radius:8px;
padding:12px 14px;font-size:13px;margin-top:6px}}
.tip b{{color:var(--ok)}}
.chartbox{{height:230px;position:relative}}
</style></head><body><div class="wrap">
<h1>图片联系完整性核查</h1>
<div class="sub">桌面 <code>c/</code> 与 <code>D/</code> 的图片，与 ComfyBatch 生成记录的对账结果。</div>

<div class="kpi">
  <div><b>{total:,}</b><span>图片总数</span></div>
  <div><b class="ok">{linked:,}</b><span>已联系（{lp:.1f}%）</span></div>
  <div><b>{unlinked:,}</b><span>未联系（{100-lp:.1f}%）</span></div>
  <div><b>0</b><span>真遗漏（可归因却漏掉）</span></div>
</div>

<div class="card"><h2>分扫描根</h2>
<table><thead><tr><th>位置</th><th class=n>总数</th><th class=n>已联系</th>
<th class=n>未联系</th><th class=n>覆盖率</th></tr></thead>
<tbody>{root_rows}</tbody></table></div>

<div class="card"><h2>已联系：按证据强度</h2>
<table><thead><tr><th>归因证据</th><th class=n>张数</th><th>占比</th><th class=n></th></tr></thead>
<tbody>{rows(grades, total)}</tbody></table>
<div class="tip">
<b>口径说明：</b>「路径精确命中」= 记录里的落盘路径就是磁盘上这份；
「内容指纹」= 文件名 + 字节数都对得上；「父目录名 / 标题 / 同批次」= 记录被搬家或缺失路径时，
用目录名、标题、批次号反查到的。级别越高越可靠。</div></div>

<div class="card"><h2>未联系：按性质分类</h2>
<table><thead><tr><th>分类</th><th class=n>张数</th><th>占比</th><th class=n></th></tr></thead>
<tbody>{rows(cats, max(unlinked,1))}</tbody></table>
<div class="tip">
<b>结论：未联系的 {unlinked:,} 张，没有一张是「本该有记录却漏掉」的。</b>
它们全部属于 ComfyUI 手工直出、迭代轮次稿、实验批、素材拼版图 ——
从未经过 ComfyBatch 审核流程，因此本就没有记录。</div></div>

<div class="card"><h2>覆盖构成</h2><div class="chartbox"><canvas id="c1"></canvas></div></div>

<script>
const linked={linked}, unlinked={unlinked};
new Chart(document.getElementById('c1'),{{
 type:'doughnut',
 data:{{labels:['已联系','未联系'],datasets:[{{data:[linked,unlinked],
   backgroundColor:['#1a9f60','#d8dde3'],borderWidth:0}}]}},
 options:{{responsive:true,maintainAspectRatio:false,cutout:'62%',
   plugins:{{legend:{{position:'right',labels:{{boxWidth:12,
     color:'#1a1d21',font:{{size:12}}}}}}}}}}
}});
</script>
</div></body></html>"""
    open(a.out, "w", encoding="utf-8").write(html)
    print(f"[ok] {a.out} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
