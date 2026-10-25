"""Offline regressions for hidden BLACK edges, display names and prompt-only crowds."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import canvas as c
import canvas_reference_report as report
import test_canvas_reference_workflow as fixtures


class InterruptedEdgeCLI(fixtures.WorkflowCLI):
    def __init__(self, doc, apply_first):
        super().__init__(doc)
        self.apply_first = apply_first
        self.interrupted = False

    def command(self, canvas_id, name, value=None):
        if name == "create_edge" and not self.interrupted:
            self.interrupted = True
            if self.apply_first:
                super().command(canvas_id, name, value)
            else:
                self.calls.append(name)
                self.writes.append((name, copy.deepcopy(value)))
            c.fail("CLI_ERROR", "mock lost edge acknowledgement")
        return super().command(canvas_id, name, value)


class EdgeOnlyChecks(unittest.TestCase):
    sync = fixtures.ReferenceWorkflowChecks.sync
    prepared = fixtures.ReferenceWorkflowChecks.prepared

    def setUp(self):
        fixtures.ReferenceWorkflowChecks.setUp(self)
        self.unit = self.session["units"][0]
        self.unit.update(black_reference_mode="edge-only-v1", display_name="A01-场景1-分镜1",
                         test_prompt="A01-场景1-分镜1（时长：4秒）\n@Actor @Room @Pen action")
        self.session["units"] = [self.unit]

    def target(self):
        return c.read_state(self.project)["mappings"][self.unit["id"]]["node_id"]

    def test_new_unit_has_reference_edge_without_black_prompt_and_preserves_names(self):
        result = self.sync()
        self.assertEqual(result["status"], "synced")
        target = self.target()
        prompt = c.prompt_of(self.cli.doc, target)
        self.assertNotIn("黑幕", prompt)
        self.assertNotIn("BLACK", c.node_asset_ids(prompt))
        self.assertIsNotNone(c.reference_edge(self.cli.doc, "BLACK", target))
        self.assertEqual({c.content(self.cli.doc, target)[field] for field in ("name", "title", "caption")},
                         {self.unit["display_name"]})
        self.assertFalse(any(row["reason"] == "missing_video" for row in result["reference_issues"]))
        self.assertNotIn("reference_not_encoded", {row["reason"] for row in result["reference_issues"]})
        self.assertIn(self.unit["display_name"], Path(result["reference_report"]).read_text(encoding="utf-8"))
        receipt = json.loads(Path(result["video_write_receipts"][0]).read_text(encoding="utf-8"))
        self.assertTrue(receipt["references_verified"])
        self.assertIn("reference_edge", [check["action"] for check in receipt["checks"]])
        edge_index = next(i for i, (name, _) in enumerate(self.cli.writes) if name == "create_edge")
        prompt_index = next(i for i, (name, _) in enumerate(self.cli.writes) if name == "xyq.generation.update_prompt")
        self.assertLess(edge_index, prompt_index)

    def test_repeat_refresh_is_idempotent_and_repairs_deleted_edge_without_prompt_rewrite(self):
        self.sync()
        target = self.target()
        self.cli.writes.clear()
        self.sync(existing_only=True, references_only=True)
        self.assertEqual(self.cli.writes, [])
        edge = c.reference_edge(self.cli.doc, "BLACK", target)
        del self.cli.doc["assets"]["ROOT"]["content"]["edges"][edge["id"]]
        result = self.sync(existing_only=True, references_only=True)
        self.assertEqual(result["status"], "synced")
        self.assertEqual([name for name, _ in self.cli.writes], ["create_edge"])

    def test_existing_edge_is_reused_and_unrelated_edges_preserved(self):
        self.prepared()
        target = self.unit["id"]
        edges = self.cli.doc["assets"]["ROOT"]["content"]["edges"]
        edges["manual"] = {"id": "manual", "type": "reference", "source": "BLACK", "target": target}
        edges["unrelated"] = {"id": "unrelated", "type": "reference", "source": "EXTRA", "target": target}
        self.sync()
        self.assertNotIn("create_edge", [name for name, _ in self.cli.writes])
        self.assertIn("unrelated", edges)
        self.assertEqual(c.read_state(self.project)["mappings"][self.unit["id"]]["black_reference_edge_id"], "manual")

    def test_unknown_ack_with_applied_edge_resumes_without_duplicate(self):
        self.cli = InterruptedEdgeCLI(self.cli.doc, True)
        with self.assertRaises(c.CanvasError) as exc:
            self.sync()
        self.assertEqual(exc.exception.code, "UNCERTAIN_WRITE")
        target = self.target()
        state = c.read_state(self.project)
        entry = next(row for row in state["journal"] if row["command"] == "create_edge")
        self.assertEqual(entry["status"], "uncertain")
        self.assertEqual(entry["input"]["edge"]["target"], target)
        result = self.sync()
        self.assertEqual(result["status"], "synced")
        self.assertEqual(self.target(), target)
        self.assertEqual([name for name, _ in self.cli.writes].count("create_edge"), 1)

    def test_unknown_ack_without_edge_blocks_replay(self):
        self.cli = InterruptedEdgeCLI(self.cli.doc, False)
        with self.assertRaises(c.CanvasError):
            self.sync()
        before = len(self.cli.writes)
        with self.assertRaises(c.CanvasError) as exc:
            self.sync()
        self.assertEqual(exc.exception.code, "UNCERTAIN_WRITE")
        self.assertEqual(len(self.cli.writes), before)

    def test_ack_without_actual_edge_cannot_report_completed(self):
        second = copy.deepcopy(self.unit)
        second.update(id="场景1-分镜2", display_name="A02-场景1-分镜2",
                      test_prompt="A02-场景1-分镜2（时长：4秒）\n@Actor @Room @Pen action")
        self.session["units"].append(second)
        self.prepared()
        self.cli.omit_edges_for.add(self.unit["id"])
        with patch.object(c, "VIDEO_READBACK_DELAY", 0):
            result = self.sync()
        self.assertEqual(result["status"], "partial")
        state = c.read_state(self.project)
        receipt = json.loads(Path(state["mappings"][self.unit["id"]]["video_write_receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "partial")
        self.assertTrue(receipt["core_verified"])
        self.assertFalse(receipt["references_verified"])
        edge_step = next(row for row in state["journal"] if row["command"] == "create_edge")
        self.assertEqual(edge_step["status"], "partial")
        self.assertIn(self.unit["id"], state["pending_units"])
        self.assertTrue(state["mappings"][second["id"]]["references_bound"])
        self.assertIsNotNone(c.reference_edge(self.cli.doc, "BLACK", second["id"]))
        self.cli.omit_edges_for.clear()
        result = self.sync()
        self.assertEqual(result["status"], "synced")
        self.assertEqual(self.target(), self.unit["id"])
        self.assertEqual(self.cli.allocations, [])

    def test_black_prompt_tokens_rejected_before_video_write(self):
        self.unit["test_prompt"] += "\n@黑幕"
        result = self.sync()
        self.assertIn("prompt_encoding_failed", {row["reason"] for row in result["failures"]})
        self.assertNotIn(self.unit["id"], c.read_state(self.project)["mappings"])

    def test_hidden_black_missing_or_wrong_edge_still_reported(self):
        self.sync()
        target = self.target()
        edge = c.reference_edge(self.cli.doc, "BLACK", target)
        edge["source"] = "EXTRA"
        issues = report.collect_issues(self.session, c.read_state(self.project), self.cli.doc, [self.unit])
        self.assertIn(("BLACK", "reference_edge_missing"), {(row["key"], row["reason"]) for row in issues})
        c.content(self.cli.doc, target)["generation"]["prompt"] += "\n<node-asset>BLACK</node-asset>"
        issues = report.collect_issues(self.session, c.read_state(self.project), self.cli.doc, [self.unit])
        self.assertIn("black_prompt_present", {row["reason"] for row in issues})

    def test_prompt_only_crowd_is_not_visual_asset_or_missing_image(self):
        self.session["ledger"]["characters"].append({"id": "CHAR2", "name": "Crowd", "visual_mode": "prompt_only"})
        self.unit["character_ids"].append("CHAR2")
        self.unit["test_prompt"] += " Crowd watching"
        self.assertNotIn("CHAR2", c.unit_asset_ids(self.session, self.unit))
        refs, pending = c.reference_map(self.session, self.state, self.cli.doc)
        self.assertNotIn("CHAR2", {row["key"] for row in pending})
        result = self.sync()
        self.assertNotIn("CHAR2", c.read_state(self.project)["mappings"])
        self.assertNotIn("CHAR2", {row["key"] for row in result["reference_issues"]})
        images = self.project / "images"
        images.mkdir()
        (images / "Crowd.png").write_bytes(b"\x89PNG\r\n\x1a\nignored")
        scanned = c.media.scan_images(self.session, images)
        self.assertNotIn("CHAR2", {row["key"] for row in scanned["matched"] + scanned["missing"]})

    def test_display_name_and_short_alias_select_same_internal_key(self):
        self.assertEqual(c.selected_units(self.session, ids=["A01"]), [self.unit])
        self.assertEqual(c.selected_units(self.session, ids=[self.unit["display_name"]]), [self.unit])


if __name__ == "__main__":
    unittest.main()
