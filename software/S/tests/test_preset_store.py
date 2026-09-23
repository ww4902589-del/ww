"""Preset persistence must not resurrect what the user deleted.

The bug this pins down: the store used to read every candidate settings file and
**union** the preset map across them, while ``save`` only raised if *all* writes
failed. One stale copy -- a failed mirror write, or an update that shipped an old
file into the installation directory -- put a deleted preset straight back.

The rules now under test:

* entities come from a single winning file, never a blind union;
* deletions are recorded as tombstones and the tombstone set is *unioned*, so a
  delete in any copy wins;
* the install-directory copy is read-only and can never reintroduce old data;
* only an explicit restore may bring a deleted shipped preset back.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import SOURCE_ROOT  # noqa: E402

import comfybatch_v2_app as app_module  # noqa: E402
from comfybatch_v2_app import (  # noqa: E402
    DEFAULT_STYLE_LORA_PRESETS,
    SCHEMA_VERSION,
    Application,
    SettingsStore,
)


def preset(preset_id: str, name: str) -> dict:
    return {"id": preset_id, "name": name, "styles": [], "loras": []}


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.primary = self.root / "data" / "settings.json"
        self.backup = self.root / "data" / "settings.backup.json"
        self.legacy = self.root / "install" / "S-data" / "settings.json"

    def tearDown(self):
        self.temp.cleanup()

    def store(self, *, with_backup: bool = True, with_legacy: bool = False) -> SettingsStore:
        return SettingsStore(
            self.primary,
            self.backup if with_backup else None,
            [self.legacy] if with_legacy else [],
        )

    def write(self, path: pathlib.Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def read(self, path: pathlib.Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------ versioning

    def test_first_save_stamps_schema_and_revision(self):
        store = self.store()
        store.save({"style_lora_presets": {"a": preset("a", "A")}})
        written = self.read(self.primary)
        self.assertEqual(SCHEMA_VERSION, written["schema_version"])
        self.assertEqual(1, written["revision"])
        self.assertTrue(written["updated_at"])

    def test_revision_increases_with_each_save(self):
        store = self.store()
        store.save({"style_lora_presets": {}})
        store.save({"style_lora_presets": {}})
        self.assertEqual(2, self.read(self.primary)["revision"])

    def test_backup_mirrors_the_latest_write_so_recovery_is_lossless(self):
        store = self.store()
        store.save({"lora_profiles": {"one": {"display_name": "一"}}})
        store.save({"lora_profiles": {"one": {"display_name": "一"}, "two": {"display_name": "二"}}})
        self.assertEqual(self.read(self.primary), self.read(self.backup))
        self.assertIn("two", self.read(self.backup)["lora_profiles"])

    def test_a_corrupt_primary_recovers_from_the_backup(self):
        store = self.store()
        store.save({"lora_profiles": {"kept": {"display_name": "保留"}}})
        self.primary.write_text("{ not json", encoding="utf-8")
        self.assertIn("kept", self.store().load()["lora_profiles"])

    def test_a_failed_primary_write_is_reported(self):
        """Silently continuing is what produced stale mirrors."""
        store = self.store()
        blocker = self.root / "blocked"
        blocker.mkdir()
        store.primary = blocker  # a directory cannot be written as a file
        with self.assertRaises(OSError):
            store.save({"style_lora_presets": {"a": preset("a", "A")}})

    # ------------------------------------------------------------- authority

    def test_entities_come_from_one_file_not_a_union(self):
        """The old union put this entry back; there is no union any more."""
        fresh = {"revision": 5, "updated_at": "2026-01-02 00:00:00", "style_lora_presets": {"a": preset("a", "A")}}
        stale = {"revision": 1, "updated_at": "2026-01-01 00:00:00",
                 "style_lora_presets": {"a": preset("a", "A"), "gone": preset("gone", "G")}}
        self.write(self.primary, fresh)
        self.write(self.backup, stale)
        loaded = self.store().load()
        self.assertIn("a", loaded["style_lora_presets"])
        self.assertNotIn("gone", loaded["style_lora_presets"], "低版本文件里的条目不得被并回来")

    def test_the_higher_revision_wins_over_a_newer_mtime(self):
        newer_revision = {"revision": 9, "style_lora_presets": {"x": preset("x", "X")}}
        older_revision = {"revision": 2, "style_lora_presets": {"y": preset("y", "Y")}}
        self.write(self.primary, newer_revision)
        self.write(self.backup, older_revision)
        # Touch the lower-revision file so mtime would prefer it.
        import os
        import time

        os.utime(self.backup, (time.time() + 60, time.time() + 60))
        loaded = self.store().load()
        self.assertIn("x", loaded["style_lora_presets"])
        self.assertNotIn("y", loaded["style_lora_presets"], "revision 优先于 mtime")

    # ------------------------------------------------------------ tombstones

    def test_a_delete_is_not_undone_by_a_stale_copy(self):
        """The exact regression: delete, then a stale file still has it."""
        store = self.store()
        store.save({"style_lora_presets": {"a": preset("a", "A"), "b": preset("b", "B")},
                    "deleted_style_lora_presets": {}})
        # User deletes "a"; the tombstone is written with the new content.
        store.save({"style_lora_presets": {"b": preset("b", "B")},
                    "deleted_style_lora_presets": {"a": {"deleted_at": "2026-01-02 00:00:00", "name": "A"}}})

        # A stale mirror still carrying "a" must not bring it back.
        self.write(self.backup, {"style_lora_presets": {"a": preset("a", "A"), "b": preset("b", "B")}})
        loaded = self.store().load()
        self.assertNotIn("a", loaded["style_lora_presets"], "已删除的预设不得复活")
        self.assertIn("b", loaded["style_lora_presets"])

    def test_tombstones_are_unioned_across_copies(self):
        """A delete recorded in any copy wins, even if it is not the winner."""
        self.write(self.primary, {
            "revision": 1, "style_lora_presets": {"a": preset("a", "A")},
        })
        self.write(self.backup, {
            "revision": 2, "style_lora_presets": {"a": preset("a", "A")},
            "deleted_style_lora_presets": {"a": {"deleted_at": "2026-01-01 00:00:00"}},
        })
        loaded = self.store().load()
        self.assertNotIn("a", loaded["style_lora_presets"])

    def test_a_lora_profile_delete_also_sticks(self):
        self.write(self.primary, {"style_lora_presets": {},
                                  "lora_profiles": {"l.safetensors": {"display_name": "L"}},
                                  "deleted_lora_profiles": {"l.safetensors": {"deleted_at": "x"}}})
        self.assertNotIn("l.safetensors", self.store().load()["lora_profiles"])

    # ---------------------------------------------------------------- legacy

    def test_the_install_directory_copy_is_never_written(self):
        self.write(self.legacy, {"style_lora_presets": {"shipped": preset("shipped", "S")}})
        store = self.store(with_legacy=True)
        before = self.legacy.read_text(encoding="utf-8")
        store.save({"style_lora_presets": {"mine": preset("mine", "M")}})
        self.assertEqual(before, self.legacy.read_text(encoding="utf-8"),
                         "更新不得覆盖安装目录里的旧文件")
        self.assertFalse(self.legacy.exists() and "mine" in self.read(self.legacy)["style_lora_presets"])

    def test_legacy_data_is_migrated_on_first_load(self):
        self.write(self.legacy, {"style_lora_presets": {"shipped": preset("shipped", "S")}})
        loaded = self.store(with_legacy=True).load()
        self.assertIn("shipped", loaded["style_lora_presets"], "旧位置的数据必须被迁移进来")

    def test_legacy_cannot_resurrect_after_migration(self):
        """Once migrated, deleting must stick even though the legacy file remains."""
        self.write(self.legacy, {"style_lora_presets": {"shipped": preset("shipped", "S")}})
        store = self.store(with_legacy=True)
        migrated = store.load()
        store.save({**migrated,
                    "style_lora_presets": {},
                    "deleted_style_lora_presets": {"shipped": {"deleted_at": "2026-01-01 00:00:00"}}})
        self.assertNotIn("shipped", self.store(with_legacy=True).load()["style_lora_presets"])


class DeletedPresetUiTests(unittest.TestCase):
    """Deleted presets stay tombstoned internally; V2.19 exposes a guarded
    restore-defaults button but still renders no deleted-presets panel."""

    def test_restore_defaults_is_exposed_but_no_deleted_panel(self):
        from fakes import page_source

        html = page_source()
        self.assertIn("restoreDefaultPresets(this)", html)
        for marker in ('id="deletedPresets"', "refreshDeletedPresets"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, html)

    def test_delete_action_remains_available_without_deleted_list_rendering(self):
        from fakes import page_source

        html = page_source()
        self.assertIn("deletePreset()", html)
        self.assertNotIn("/api/deleted-presets", html)

    def test_deleted_presets_are_not_rendered_as_a_visible_label(self):
        from fakes import js_source

        js = js_source()
        self.assertNotIn("已删除预设", js)
        self.assertIn("label=p?.name||''", js)


class PackagingTests(unittest.TestCase):
    def test_the_preset_template_ships_with_the_program(self):
        """Otherwise "恢复出厂预设" has nothing to restore from a packaged build."""
        import fakes

        for name in ("S.spec", "ComfyBatch-V2.spec"):
            source = (fakes.SOURCE_ROOT / name).read_text(encoding="utf-8")
            with self.subTest(spec=name):
                self.assertIn("default_presets.json", source,
                              "打包定义必须带上出厂预设模板")


class ImportIsReadOnlyTests(unittest.TestCase):
    """Constructing Application must not write anything.

    Found by accident: importing this module ran a startup save, so a diagnostic
    one-liner rewrote the user's real settings file. Any test, script or tool that
    imports the app would do the same.
    """

    def test_constructing_an_application_does_not_touch_the_files(self):
        import hashlib

        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            primary = root / "data" / "settings.json"
            backup = root / "data" / "settings.backup.json"
            primary.parent.mkdir(parents=True, exist_ok=True)
            primary.write_text(json.dumps({"lora_profiles": {}, "style_lora_presets": {}}), encoding="utf-8")
            before = (primary.stat().st_mtime, hashlib.sha256(primary.read_bytes()).hexdigest())

            Application(settings_path=primary, backup_settings_path=backup)

            after = (primary.stat().st_mtime, hashlib.sha256(primary.read_bytes()).hexdigest())
            self.assertEqual(before, after, "构造 Application 不得写入任何文件")
            self.assertFalse(backup.exists(), "构造时不应创建备份文件")

    def test_the_migrated_view_is_still_available_in_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            primary = root / "settings.json"
            primary.write_text(json.dumps({
                "lora_profiles": {"l.safetensors": {"display_name": "老"}},
                "style_lora_presets": {"old": preset("old", "旧")},
            }, ensure_ascii=False), encoding="utf-8")
            application = Application(settings_path=primary, backup_settings_path=None)
            # Readable without a write, which is what makes a read-only migration safe.
            self.assertIn("old", application.style_lora_presets())
            self.assertEqual(SCHEMA_VERSION, application._settings.get("schema_version"))


class ShippedDefaultsTests(unittest.TestCase):
    def test_the_template_is_a_separate_file_with_stable_ids(self):
        path = SOURCE_ROOT / "default_presets.json"
        self.assertTrue(path.exists(), "出厂预设模板必须是独立文件")
        self.assertGreaterEqual(len(DEFAULT_STYLE_LORA_PRESETS), 10)
        for entry in DEFAULT_STYLE_LORA_PRESETS:
            with self.subTest(preset=entry.get("id")):
                self.assertTrue(entry.get("id"))
                self.assertTrue(entry.get("name"))

    def test_the_template_is_not_user_data(self):
        """It must not carry deletion records or a revision, or it would fight the user's file."""
        payload = json.loads((SOURCE_ROOT / "default_presets.json").read_text(encoding="utf-8"))
        self.assertNotIn("deleted_style_lora_presets", payload)
        self.assertNotIn("revision", payload)


class ApplicationPresetTests(unittest.TestCase):
    """Through the Application, which is where the user actually deletes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.primary = self.root / "data" / "settings.json"
        self.backup = self.root / "data" / "settings.backup.json"

    def tearDown(self):
        self.temp.cleanup()

    def app(self) -> Application:
        return Application(settings_path=self.primary, backup_settings_path=self.backup)

    def seed(self, application: Application) -> str:
        preset_id = "user-made-01"
        application.save_style_lora_preset({"name": "用户预设", "styles": [], "loras": [{"name": "x.safetensors", "strength": 0.5}]})
        presets = application._settings["style_lora_presets"]
        self.assertTrue(presets)
        return next(iter(presets))

    def test_delete_writes_a_tombstone(self):
        application = self.app()
        preset_id = self.seed(application)
        application.delete_style_lora_preset(preset_id)
        deleted = application.deleted_style_lora_presets()
        self.assertIn(preset_id, deleted)
        self.assertTrue(deleted[preset_id].get("deleted_at"))

    def test_the_deletion_survives_a_restart_with_a_stale_mirror(self):
        application = self.app()
        preset_id = self.seed(application)
        snapshot = self.read_primary()
        application.delete_style_lora_preset(preset_id)

        # Simulate the old failure mode: a stale copy still holding the preset.
        self.backup.parent.mkdir(parents=True, exist_ok=True)
        self.backup.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

        restarted = self.app()
        self.assertNotIn(preset_id, restarted.style_lora_presets(), "重新启动后已删除的预设不得复活")

    def test_restore_is_explicit_and_only_affects_shipped_presets(self):
        application = self.app()
        shipped_id = str(DEFAULT_STYLE_LORA_PRESETS[0]["id"])
        application._settings.setdefault("style_lora_presets", {})[shipped_id] = \
            json.loads(json.dumps(DEFAULT_STYLE_LORA_PRESETS[0]))
        application.delete_style_lora_preset(shipped_id)
        self.assertNotIn(shipped_id, application.style_lora_presets())

        result = application.restore_default_style_lora_presets()
        self.assertIn(shipped_id, result["restored"])
        self.assertIn(shipped_id, application.style_lora_presets())
        self.assertNotIn(shipped_id, application.deleted_style_lora_presets())

    def test_restore_does_not_touch_user_made_presets(self):
        application = self.app()
        preset_id = self.seed(application)
        application.delete_style_lora_preset(preset_id)
        result = application.restore_default_style_lora_presets()
        self.assertEqual([], result["restored"], "出厂模板里没有的预设不得被恢复")
        self.assertNotIn(preset_id, application.style_lora_presets())

    def test_deleting_twice_is_refused(self):
        application = self.app()
        preset_id = self.seed(application)
        application.delete_style_lora_preset(preset_id)
        with self.assertRaisesRegex(ValueError, "已经不存在"):
            application.delete_style_lora_preset(preset_id)

    def test_migration_stamps_the_new_schema(self):
        self.primary.parent.mkdir(parents=True, exist_ok=True)
        self.primary.write_text(json.dumps({
            "lora_profiles": {"l.safetensors": {"display_name": "老数据"}},
            "style_lora_presets": {"old": preset("old", "旧预设")},
        }, ensure_ascii=False), encoding="utf-8")

        application = self.app()
        # The migrated shape is available immediately, without a write...
        self.assertIn("old", application.style_lora_presets())
        self.assertIn("l.safetensors", application.lora_profiles())
        self.assertEqual(SCHEMA_VERSION, application._settings["schema_version"])
        self.assertNotIn("schema_version", self.read_primary(), "构造时不得写回文件")

        # ...and the file is stamped by the next real write.
        application.save_lora_profile({"name": "later.safetensors", "display_name": "之后"})
        written = self.read_primary()
        self.assertEqual(SCHEMA_VERSION, written["schema_version"])
        self.assertGreaterEqual(written["revision"], 1)

    def read_primary(self) -> dict:
        return json.loads(self.primary.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
