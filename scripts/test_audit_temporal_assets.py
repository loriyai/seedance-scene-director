"""Isolated behavioral verification for reviewed temporal SET versions."""
import copy
import json
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path

MODULE = Path(__file__).resolve().parent.parent / "modules" / "screenplay-standardizer" / "scripts"
sys.path.insert(0, str(MODULE))
import asset_ledger as ledger
import asset_migrate as migration

WORK = None


def fixture(name, texts, places=None):
    directory = WORK / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "scenes").mkdir(exist_ok=True)
    places = places or ["陈家老宅"] * len(texts)
    locations = list(dict.fromkeys(places))
    paragraphs = []
    scenes, script = [], []
    for n, (text, place) in enumerate(zip(texts, places), 1):
        lines = text if isinstance(text, list) else [text]
        start = len(paragraphs) + 1
        paragraphs.extend(lines)
        heading = f"场景{n}-日-内-{place}"
        body = heading + "\n人物：无；\n" + "\n".join("△ " + line for line in lines) + "\n"
        (directory / "scenes" / f"scene_{n:03d}_{start:04d}-{len(paragraphs):04d}.txt").write_text(body, encoding="utf-8")
        script.append(body)
        scenes.append({"id": f"SC{n:04d}", "number": n, "heading": heading,
            "source_range": [start, len(paragraphs)], "location_id": f"LOC{locations.index(place) + 1:04d}",
            "scene_asset_id": None, "character_ids": [], "prop_ids": [], "visual_facts": [],
            "time_basis": {"kind": "格式默认", "source_paragraphs": []}})
    (directory / "source.json").write_text(json.dumps({"paragraphs": [{"id": n, "text": t} for n, t in enumerate(paragraphs, 1)]}, ensure_ascii=False), encoding="utf-8")
    (directory / "standard.txt").write_text("\n".join(script), encoding="utf-8")
    data = {"schema_version": 3, "source_digest": ledger.digest(paragraphs), "scenes": scenes,
        "locations": [{"id": f"LOC{n:04d}", "name": p, "aliases": [], "visual_facts": [], "identity_status": "confirmed", "identity_basis": "核实为同一主体的同一空间"} for n, p in enumerate(locations, 1)],
        "scene_assets": [], "characters": [], "props": [], "retired_ids": [], "pending_reviews": [], "inactive_assets": {"characters": []}}
    return directory, paragraphs, data


def layer(data, ident, label, scene_number, until=None):
    scene = data["scenes"][scene_number - 1]
    data.setdefault("temporal_layers", []).append({"id": ident, "label": label, "start_scene_id": scene["id"],
        "evidence": label, "source_paragraphs": [scene["source_range"][0]], "review_basis": "叙述明确切入新的故事时间，人工确认属于较大跨度而非对白回述"})
    for s in data["scenes"][scene_number - 1:until]:
        s["temporal_layer_id"] = ident


def reuse(data, n, target, evidence):
    s = data["scenes"][n - 1]
    s["set_reuse"] = {"same_as_scene_id": f"SC{target:04d}", "evidence": evidence,
        "source_paragraphs": [s["source_range"][0]]}


class TemporalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global WORK
        cls.temporary = tempfile.TemporaryDirectory(prefix="seedance-temporal-assets-")
        WORK = Path(cls.temporary.name)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def validate(self, directory, p, d):
        ledger.validate(ledger.parse_script(directory / "standard.txt"), p, d)

    def test_multiple_jumps_same_layer_and_late_first_location(self):
        directory, p, d = fixture("jumps", ["宅中摆着木桌。", "3年后。他回到宅中。", "翌日，他仍在宅中。", "十年后。他再次回来。", "同年，来到江边书房。"], ["陈家老宅"] * 4 + ["江边书房"])
        layer(d, "TL1", "3年后", 2, 3)
        layer(d, "TL2", "十年后", 4)
        d["retired_ids"] = ["SET0009"]
        built = ledger.build_scene_assets(d, p)
        self.assertEqual([(a["name"], a["variant"]) for a in built["scene_assets"]], [("陈家老宅", "base"), ("江边书房", "base"), ("陈家老宅-3年后", "temporal"), ("陈家老宅-十年后", "temporal")])
        self.assertEqual(built["scenes"][1]["scene_asset_id"], built["scenes"][2]["scene_asset_id"])
        self.assertEqual(built["scene_assets"][3]["parent_id"], built["scene_assets"][2]["id"])
        self.assertEqual(built, ledger.build_scene_assets(built, p))
        self.validate(directory, p, built)
        (directory / "reviewed.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        output = directory / "cli-built.json"
        output.unlink(missing_ok=True)
        subprocess.run([sys.executable, str(MODULE / "asset_ledger.py"), "build-scenes", str(directory / "reviewed.json"), "--source", str(directory / "source.json"), "--output", str(output)], check=True, capture_output=True, text=True)
        subprocess.run([sys.executable, str(MODULE / "asset_ledger.py"), "render", str(directory / "standard.txt"), str(directory / "source.json"), str(output), "--output", str(directory / "catalog.txt")], check=True, capture_output=True, text=True)

    def test_unchanged_original_and_previous_temporal_propagate(self):
        for target in (1, 2):
            directory, p, d = fixture(f"unchanged-{target}", ["老宅空着。", "三年后，他回来。", "十年后，屋内仍是那时的原样。", "翌日，他坐在屋里。"])
            layer(d, "T1", "三年后", 2, 2)
            layer(d, "T2", "十年后", 3)
            reuse(d, 3, target, "屋内仍是那时的原样")
            built = ledger.build_scene_assets(d, p)
            self.assertEqual(len(built["scene_assets"]), 2)
            self.assertEqual(built["scenes"][2]["scene_asset_id"], built["scenes"][target - 1]["scene_asset_id"])
            self.assertEqual(built["scenes"][2]["scene_asset_id"], built["scenes"][3]["scene_asset_id"])
            self.validate(directory, p, built)

    def test_dialogue_historical_reference_does_not_split(self):
        directory, p, d = fixture("historical-reference", ["他说：十年前我来过这里。", "他继续回忆旧事。"])
        built = ledger.build_scene_assets(d)
        self.assertEqual(len(built["scene_assets"]), 1)
        self.validate(directory, p, built)

    def test_invalid_evidence_and_unreviewed_layer_rejected(self):
        _, p, d = fixture("bad-evidence", ["老宅。", "三年后，他回来。", "他走进屋里。"])
        layer(d, "T1", "三年后", 2)
        with self.assertRaisesRegex(ValueError, "--source"):
            ledger.build_scene_assets(d)
        bad = copy.deepcopy(d)
        bad["temporal_layers"][0]["evidence"] = "十年后"
        with self.assertRaisesRegex(ValueError, "证据"):
            ledger.build_scene_assets(bad, p)
        bad = copy.deepcopy(d)
        del bad["scenes"][2]["temporal_layer_id"]
        with self.assertRaisesRegex(ValueError, "实际时间层"):
            ledger.build_scene_assets(bad, p)

    def test_explicit_flashback_to_initial_layer(self):
        directory, p, d = fixture("flashback", ["他在宅中。", "三年后，他回来。", "回忆当年在宅中。", "回到三年后的宅中。"])
        layer(d, "T1", "三年后", 2)
        d["scenes"][2]["temporal_layer_id"] = None
        built = ledger.build_scene_assets(d, p)
        self.assertEqual(built["scenes"][0]["scene_asset_id"], built["scenes"][2]["scene_asset_id"])
        self.assertEqual(built["scenes"][1]["scene_asset_id"], built["scenes"][3]["scene_asset_id"])
        self.validate(directory, p, built)

    def test_same_label_distinct_layers_and_existing_links_rejected(self):
        _, p, d = fixture("collisions", ["旧宅。", "三年后，他回来。", "三年后，他再回来。"])
        layer(d, "T1", "三年后", 2, 2)
        layer(d, "T2", "三年后", 3)
        with self.assertRaisesRegex(ValueError, "重复"):
            ledger.build_scene_assets(d, p)
        _, p, d = fixture("old-link", ["旧宅。", "三年后，他回来。"])
        old = ledger.build_scene_assets(d)
        before = copy.deepcopy(old)
        layer(old, "T1", "三年后", 2)
        with self.assertRaisesRegex(ValueError, "迁移"):
            ledger.build_scene_assets(old, p)
        self.assertEqual(old["scenes"][1]["scene_asset_id"], before["scenes"][1]["scene_asset_id"])

    def test_decor_common_inheritance_and_parent_cycles(self):
        directory, p, d = fixture("decor-parent", ["屋有石柱，布置成婚堂，挂满红绸。", "十年后，他回到宅中。"])
        layer(d, "T1", "十年后", 2)
        d["locations"][0]["visual_facts"] = [{"text": "石柱", "evidence": "石柱", "source_paragraphs": [1], "scene_ids": ["SC0001"], "attribution": "叙述"}]
        d["scene_assets"] = [{"id": "SET0001", "name": "陈家老宅", "location_id": "LOC0001", "variant": "base", "parent_id": None, "visual_facts": []},
            {"id": "SET0002", "name": "陈家老宅-婚堂", "location_id": "LOC0001", "variant": "decor", "parent_id": "SET0001", "visual_facts": [{"text": "红绸", "evidence": "红绸", "source_paragraphs": [1], "scene_ids": ["SC0001"], "attribution": "叙述"}]}]
        d["scenes"][0]["scene_asset_id"] = "SET0002"
        built = ledger.build_scene_assets(d, p)
        self.assertEqual(built["scene_assets"][2]["parent_id"], "SET0002")
        facts = ledger.set_visual_facts(built["scene_assets"][2], {x["id"]: x for x in built["locations"]}, {x["id"]: x for x in built["scene_assets"]})
        self.assertEqual([f["text"] for f in facts], ["石柱"])
        self.validate(directory, p, built)
        path = directory / "ledger.json"
        path.write_text(json.dumps(built, ensure_ascii=False), encoding="utf-8")
        ledger.render(directory / "standard.txt", directory / "source.json", path, directory / "catalog.txt")
        line = next(line for line in (directory / "catalog.txt").read_text(encoding="utf-8").splitlines() if line.startswith("03 - 陈家老宅-十年后"))
        self.assertIn("石柱", line)
        self.assertNotIn("红绸", line)
        bad = copy.deepcopy(built)
        bad["scene_assets"][2]["parent_id"] = bad["scene_assets"][2]["id"]
        with self.assertRaisesRegex(ValueError, "循环"):
            ledger.build_scene_assets(bad, p)

    def test_legacy_decor_switches_preserved(self):
        directory, p, d = fixture("legacy-decor", ["石柱。", "婚堂有红绸。", "回到基础布景。"])
        d["scene_assets"] = [{"id": "SET0001", "name": "陈家老宅", "location_id": "LOC0001", "variant": "base", "parent_id": None, "visual_facts": []}, {"id": "SET0002", "name": "陈家老宅-婚堂", "location_id": "LOC0001", "variant": "decor", "parent_id": "SET0001", "visual_facts": [{"text": "红绸", "evidence": "红绸", "source_paragraphs": [2], "scene_ids": ["SC0002"], "attribution": "叙述"}]}]
        for s, aid in zip(d["scenes"], ["SET0001", "SET0002", "SET0001"]):
            s["scene_asset_id"] = aid
        built = ledger.build_scene_assets(d)
        self.assertEqual(built, d)
        self.validate(directory, p, built)

    def test_migration_keeps_or_marks_temporal_records(self):
        olddir, p, d = fixture("migration-old", ["旧宅。", "三年后，他回来。", "十年后，仍和上次一样。", "翌日。"])
        layer(d, "T1", "三年后", 2, 2)
        layer(d, "T2", "十年后", 3)
        reuse(d, 3, 2, "仍和上次一样")
        d = ledger.build_scene_assets(d, p)
        oldpath = olddir / "ledger.json"
        oldpath.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        migrated, report = migration.migrate(oldpath, olddir / "source.json", olddir / "standard.txt", olddir, {})
        self.assertEqual(migrated["temporal_layers"], d["temporal_layers"])
        self.assertEqual(migrated["scenes"][2]["set_reuse"], d["scenes"][2]["set_reuse"])
        self.assertEqual(report["pending_reviews"], [])
        self.validate(olddir, p, migrated)
        newdir, newp, _ = fixture("migration-changed", ["旧宅。", "四年后，他回来。", "十年后，仍和上次一样。", "翌日。"])
        migrated, report = migration.migrate(oldpath, olddir / "source.json", newdir / "standard.txt", newdir, {"scenes": {"SC0002": [2]}})
        self.assertTrue(report["pending_reviews"])
        self.assertEqual(len(migrated["temporal_layers"]), 2)
        self.assertEqual([a["id"] for a in migrated["scene_assets"]], [a["id"] for a in d["scene_assets"]])
        with self.assertRaisesRegex(ValueError, "待核"):
            self.validate(newdir, newp, migrated)

    def test_migration_relocates_source_without_losing_anchors(self):
        olddir, p, d = fixture("migration-shift-old", ["旧宅。", "三年后，他回来。", "十年后，仍和上次一样。"])
        layer(d, "T1", "三年后", 2, 2)
        layer(d, "T2", "十年后", 3)
        reuse(d, 3, 2, "仍和上次一样")
        d = ledger.build_scene_assets(d, p)
        path = olddir / "ledger.json"
        path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        newdir, _, _ = fixture("migration-shift-new", ["开场，他在江边。", "旧宅。", "三年后，他回来。", "十年后，仍和上次一样。"], ["江边", "陈家老宅", "陈家老宅", "陈家老宅"])
        revised, report = migration.migrate(path, olddir / "source.json", newdir / "standard.txt", newdir, {})
        self.assertEqual([l["source_paragraphs"] for l in revised["temporal_layers"]], [[3], [4]])
        self.assertEqual([l["start_scene_id"] for l in revised["temporal_layers"]], ["SC0002", "SC0003"])
        reused = next(s for s in revised["scenes"] if s["id"] == "SC0003")
        self.assertEqual(reused["set_reuse"]["source_paragraphs"], [4])
        self.assertEqual(reused["set_reuse"]["same_as_scene_id"], "SC0002")
        self.assertFalse(any("时间层" in item["kind"] and item.get("old_scene_id") for item in report["pending_reviews"]))

    def test_split_migration_preserves_complete_reuse_for_review(self):
        olddir, p, d = fixture("migration-split-old", ["旧宅。", "三年后，他回来。", ["十年后，仍和上次一样。", "他坐下。"]])
        layer(d, "T1", "三年后", 2, 2)
        layer(d, "T2", "十年后", 3)
        reuse(d, 3, 2, "仍和上次一样")
        d = ledger.build_scene_assets(d, p)
        path = olddir / "ledger.json"
        path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        newdir, _, _ = fixture("migration-split-new", ["旧宅。", "三年后，他回来。", "十年后，仍和上次一样。", "他坐下。"])
        revised, report = migration.migrate(path, olddir / "source.json", newdir / "standard.txt", newdir,
            {"scenes": {"SC0003": [3, 4]}, "primary_ids": {"3": "SC0003", "4": None}})
        pending = [p for p in report["pending_reviews"] if p["kind"] == "场次实际时间层及原样基准待核"]
        self.assertTrue(pending)
        self.assertEqual(pending[0]["old_scene_records"][0]["set_reuse"], d["scenes"][2]["set_reuse"])
        self.assertEqual(pending[0]["old_scene_records"][0]["temporal_layer_id"], "T2")
        self.assertEqual(len(revised["temporal_layers"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
