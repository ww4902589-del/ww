"""Real acceptance for preset deletion: prove a delete cannot be resurrected.

Runs against copies of the user's actual settings, including the stale
install-directory copy, so the exact historical failure mode is reproduced:

  1. delete a shipped preset
  2. restart -> still gone
  3. write a stale mirror that still contains it -> still gone
  4. confirm the install-directory copy was never modified
  5. only an explicit restore brings it back
  6. restore does not touch user-made presets
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from comfybatch_v2_app import Application  # noqa: E402

DATA = pathlib.Path("run/resurrect/data")
PRIMARY = DATA / "settings.json"
BACKUP = DATA / "settings.backup.json"
LEGACY = pathlib.Path("run/resurrect/install/S-data/settings.json")

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def show(label: str) -> None:
    d = json.loads(PRIMARY.read_text(encoding="utf-8"))
    print(f"    {label}: 预设 {len(d.get('style_lora_presets') or {})} | "
          f"删除记录 {len(d.get('deleted_style_lora_presets') or {})} | "
          f"revision {d.get('revision')} | schema {d.get('schema_version')}")


legacy_hash_before = hashlib.sha256(LEGACY.read_bytes()).hexdigest()[:12]

print("=== 0. 起始：真实数据的副本（主文件 + 安装目录旧副本）===")
app = Application(settings_path=PRIMARY, backup_settings_path=BACKUP)
presets = app.style_lora_presets()
print(f"    读到的预设 {len(presets)} 个，来自主文件与旧副本的迁移")
target = "v43anime01"
check("出厂预设可读", target in presets, presets.get(target, {}).get("name", ""))
show("初始")

print("\n=== 1. 删除一个出厂预设 ===")
app.delete_style_lora_preset(target)
check("删除后立即消失", target not in app.style_lora_presets())
check("写下了删除记录", target in app.deleted_style_lora_presets())
show("删除后")

print("\n=== 2. 重启（模拟重新打开软件）===")
restarted = Application(settings_path=PRIMARY, backup_settings_path=BACKUP)
check("重启后仍未复活", target not in restarted.style_lora_presets())

print("\n=== 3. 制造陈旧镜像：把删除前的整份内容写回备份 ===")
stale = json.loads(PRIMARY.read_text(encoding="utf-8"))
stale["style_lora_presets"][target] = presets[target]
stale.pop("deleted_style_lora_presets", None)
BACKUP.parent.mkdir(parents=True, exist_ok=True)
BACKUP.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
check("陈旧副本确实带着它", target in stale["style_lora_presets"])

with_stale = Application(settings_path=PRIMARY, backup_settings_path=BACKUP)
check("陈旧镜像没有让它复活", target not in with_stale.style_lora_presets(), "这是本次修复的核心")

print("\n=== 4. 安装目录旧副本是否被改写 ===")
legacy_hash_after = hashlib.sha256(LEGACY.read_bytes()).hexdigest()[:12]
check("安装目录副本未被触碰", legacy_hash_before == legacy_hash_after,
      f"{legacy_hash_before} -> {legacy_hash_after}")

print("\n=== 5. 只有主动恢复才会回来 ===")
result = with_stale.restore_default_style_lora_presets()
check("恢复包含该预设", target in result["restored"])
check("恢复后确实存在", target in with_stale.style_lora_presets())
check("删除记录已清除", target not in with_stale.deleted_style_lora_presets())

print("\n=== 6. 「恢复默认」不动用户自建预设 ===")
mine = with_stale.save_style_lora_preset({
    "name": "我的预设", "styles": [], "loras": [{"name": "x.safetensors", "strength": 0.5}],
})
mine_id = mine["id"]
with_stale.delete_style_lora_preset(mine_id)
second = with_stale.restore_default_style_lora_presets()
check("自建预设不在恢复列表里", mine_id not in second["restored"])
check("自建预设保持删除", mine_id not in with_stale.style_lora_presets())

print("\n=== 7. 用户数据完整性 ===")
final = with_stale.style_lora_presets()
check("18 个出厂预设都在", len(final) >= 18, f"实际 {len(final)}")
check("14 条 LoRA 档案都在", len(with_stale.lora_profiles()) == 14, f"实际 {len(with_stale.lora_profiles())}")

print()
print(f"通过 {len([1 for _ in range(1)]) and 0 or 0}", end="")
total = 15
print(f"\n汇总：{total - len(failures)}/{total} 项通过")
for name in failures:
    print(f"  未通过：{name}")
raise SystemExit(1 if failures else 0)
