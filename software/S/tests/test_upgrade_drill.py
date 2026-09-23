"""升级演练：一次真实的「删掉预设 → 覆盖安装目录 → 重启」全流程。

为什么单独开一个文件
--------------------
``test_preset_store.py`` 已经把墓碑、权威文件、只读迁移逐条测过，但那些测试
都是**构造内存里的字典**去调 ``SettingsStore``。它们证明的是"逻辑对"，不是
"文件系统层面不会出事"。

这一份不一样：它在 ``tempfile`` 里搭出**三个真实文件**（用户数据、恢复副本、
安装目录里的旧副本），用真实的 ``SettingsStore`` 走一遍升级，再**重新构造一个
实例**（等价于重启），断言被删的东西没有回来。这是"删除是否会在更新后复活"
这个问题的正面回答。

用户的两个提问对应两个场景：

1. 更新**替换用户数据目录**里的文件 → 已删的预设必须仍然不在。
2. 更新**把一份旧副本放回安装目录**（旧版本会镜像到 ``<exe>/S-data/``）→
   已删的预设必须仍然不在。
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from comfybatch_v2_app import SCHEMA_VERSION, SettingsStore  # noqa: E402


def _document(presets: dict, *, revision: int = 1, tombstones: dict | None = None) -> dict:
    """A settings document shaped the way ``save`` writes one."""
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "style_lora_presets": presets,
        "lora_profiles": {},
        "deleted_style_lora_presets": tombstones or {},
        "deleted_lora_profiles": {},
    }


class UpgradeDrill(unittest.TestCase):
    """The whole point of the tombstone design, exercised on real files."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        # User data, where the app reads and writes.
        self.user_dir = self.root / "ComfyBatch-S"
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.primary = self.user_dir / "settings.json"
        self.backup = self.user_dir / "settings.backup.json"
        # The installation directory, where an update drops files. An older build
        # mirrored settings here; the new build must treat it as read-only.
        self.install_dir = self.root / "Program Files" / "S"
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.legacy = self.install_dir / "S-data" / "settings.json"

    def tearDown(self):
        self.temp.cleanup()

    def _store(self) -> SettingsStore:
        """A fresh store, as a restart would construct it."""
        return SettingsStore(self.primary, self.backup, [self.legacy])

    def _write(self, path: pathlib.Path, document: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    # --------------------------------------------------------------- scenarios

    def test_a_deleted_preset_stays_deleted_after_a_restart(self):
        """The baseline: what the user deletes does not come back on its own."""
        self._write(self.primary, _document({"a": {"name": "甲"}, "b": {"name": "乙"}}))
        store = self._store()
        self.assertEqual({"a", "b"}, set(store.load()["style_lora_presets"]))

        # Delete ``b`` the way the app does: write a tombstone, then persist.
        document = store.load()
        document["deleted_style_lora_presets"] = {"b": {"deleted_at": "2026-09-19 20:30:00"}}
        document["style_lora_presets"].pop("b", None)
        store.save(document)

        # Restart.
        after = self._store().load()
        self.assertEqual({"a"}, set(after["style_lora_presets"]))
        self.assertNotIn("b", after["style_lora_presets"])

    def test_an_update_that_ships_an_old_copy_cannot_resurrect_a_preset(self):
        """The actual bug this design exists for.

        The old build mirrored settings into the installation directory, and a
        repaired/reinstalled build could ship an old copy of that mirror. Reading
        it as an entity source put deleted presets straight back.
        """
        # State after the user deleted ``b`` earlier today.
        self._write(
            self.primary,
            _document({"a": {"name": "甲"}}, revision=7,
                      tombstones={"b": {"deleted_at": "2026-09-19 20:30:00"}}),
        )
        # An old build's mirror, still containing ``b``, sits in the install dir.
        self._write(self.legacy, _document({"a": {"name": "甲"}, "b": {"name": "乙"}}, revision=3))

        after = self._store().load()
        self.assertEqual({"a"}, set(after["style_lora_presets"]))
        self.assertNotIn("b", after["style_lora_presets"])

    def test_a_stale_install_copy_cannot_win_even_at_a_higher_revision(self):
        """Not even a *newer-looking* stale copy may reintroduce a tombstoned id."""
        self._write(
            self.primary,
            _document({"a": {"name": "甲"}}, revision=4,
                      tombstones={"b": {"deleted_at": "2026-09-19 20:30:00"}}),
        )
        # Presents itself as newest by revision -- still must not win for ``b``.
        self._write(self.legacy, _document({"a": {"name": "甲"}, "b": {"name": "乙"}}, revision=99))

        after = self._store().load()
        self.assertNotIn("b", after["style_lora_presets"])

    def test_reinstalling_to_a_fresh_machine_dir_does_not_resurrect(self):
        """A reinstall wipes the install dir; user data survives elsewhere."""
        self._write(
            self.primary,
            _document({"a": {"name": "甲"}}, revision=5,
                      tombstones={"b": {"deleted_at": "2026-09-19 20:30:00"}}),
        )
        # Fresh install: no legacy file at all.
        self.legacy.unlink(missing_ok=True)
        after = self._store().load()
        self.assertEqual({"a"}, set(after["style_lora_presets"]))

    def test_the_install_directory_is_never_written_to(self):
        """``legacy`` is a migration SOURCE. If we wrote it, updates would clobber."""
        self._write(self.legacy, _document({"a": {"name": "甲"}}, revision=2))
        before = self.legacy.read_bytes()
        store = self._store()
        document = store.load()
        document["style_lora_presets"]["c"] = {"name": "丙"}
        store.save(document)
        self.assertEqual(before, self.legacy.read_bytes())

    def test_user_data_is_not_lost_when_the_primary_is_corrupt(self):
        """The recovery copy is for recovery, and must be lossless."""
        self._write(self.primary, _document({"a": {"name": "甲"}, "b": {"name": "乙"}}))
        store = self._store()
        store.save(store.load())  # writes primary + backup
        # Primary becomes unreadable (a torn write, a bad disk).
        self.primary.write_text("{ not json", encoding="utf-8")
        after = self._store().load()
        self.assertEqual({"a", "b"}, set(after["style_lora_presets"]))

    def test_a_tombstone_does_not_hide_a_preset_the_user_creates_again(self):
        """Re-creating a preset under a NEW id must work.

        Deleting is meant to be permanent for that *identity*. If the user builds
        a new preset, it carries a new id and must survive -- otherwise the
        tombstone would be over-reaching.
        """
        self._write(
            self.primary,
            _document({"a": {"name": "甲"}}, revision=2,
                      tombstones={"b": {"deleted_at": "2026-09-19 20:30:00"}}),
        )
        store = self._store()
        document = store.load()
        document["style_lora_presets"]["d"] = {"name": "乙（重建）"}
        store.save(document)

        after = self._store().load()
        self.assertEqual({"a", "d"}, set(after["style_lora_presets"]))

    def test_deleting_everything_then_updating_keeps_it_empty(self):
        """No preset should sneak back through a migration merge."""
        self._write(self.primary, _document({"a": {"name": "甲"}, "b": {"name": "乙"}}))
        self._write(self.legacy, _document({"a": {"name": "甲"}, "b": {"name": "乙"}}, revision=1))
        store = self._store()
        document = store.load()
        document["deleted_style_lora_presets"] = {
            "a": {"deleted_at": "2026-09-19 20:30:00"},
            "b": {"deleted_at": "2026-09-19 20:30:00"},
        }
        document["style_lora_presets"] = {}
        store.save(document)

        after = self._store().load()
        self.assertEqual(set(), set(after["style_lora_presets"]))


if __name__ == "__main__":
    unittest.main()
