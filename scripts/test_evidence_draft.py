"""Evidence formatting is source-bound preparation, never review approval."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "modules" / "screenplay-standardizer" / "scripts"
sys.path.insert(0, str(MODULE))
import evidence_draft as evidence
import asset_ledger as ledger
from asset_visual_design import validate_visible_uses


class EvidenceDraftTests(unittest.TestCase):
    def setUp(self):
        self.paragraphs = ["路口。", "守卫围住他，\n封住通道。", "众人\t齐声：站住！", "随后离去。"]
        self.selections = [{"evidence_id": "EV001", "source_paragraphs": [2, 3]}]

    def draft(self):
        return evidence.prepare_evidence_draft(self.paragraphs, self.selections, source_ref="work/source.json")

    def test_multiline_evidence_retains_raw_source_and_passes_existing_contract(self):
        before = copy.deepcopy((self.paragraphs, self.selections))
        draft = self.draft()
        record = draft["records"][0]
        self.assertEqual(draft["source_digest"], ledger.digest(self.paragraphs))
        self.assertEqual(record["raw_evidence"], self.paragraphs[1] + "\n" + self.paragraphs[2])
        self.assertEqual(record["raw_paragraphs"], [{"id": 2, "text": self.paragraphs[1]}, {"id": 3, "text": self.paragraphs[2]}])
        self.assertNotIn("\n", record["evidence"])
        self.assertNotIn("\t", record["evidence"])
        fields = evidence.evidence_fields(self.paragraphs, draft, "EV001")
        scenes = {"SC0001": {"source_range": [1, 4], "character_ids": ["CHAR0001"]}}
        item = {"id": "CHAR0001", "visible_uses": [{"scene_id": "SC0001", "event_id": "围堵", **fields}]}
        validate_visible_uses(item, self.paragraphs, scenes, ledger.fact_list, "character_ids", 1, "可见出场")
        self.assertEqual((self.paragraphs, self.selections), before)

    def test_exact_excerpt_keeps_punctuation_and_word_spacing(self):
        paragraphs = ["背景：He said\nSTOP NOW!\t随后离开。"]
        draft = evidence.prepare_evidence_draft(paragraphs, [{"evidence_id": "EV1", "source_paragraphs": [1],
            "quote": "He said\nSTOP NOW!"}], source_ref="source.json")
        self.assertEqual(draft["records"][0]["evidence"], "He said STOP NOW!")
        self.assertEqual(draft["records"][0]["raw_evidence"], "He said\nSTOP NOW!")

    def test_gaps_duplicates_reverse_boolean_and_out_of_range_rejected(self):
        for refs in ([1, 3], [2, 2], [3, 2], [True], [0], [5], []):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                evidence.prepare_evidence_draft(self.paragraphs, [{"evidence_id": "EV1", "source_paragraphs": refs}], source_ref="source.json")

    def test_paraphrase_and_unused_endpoints_cannot_be_laundered(self):
        for selection in (
            {"evidence_id": "EV1", "source_paragraphs": [2], "quote": "守卫热情迎接他。"},
            {"evidence_id": "EV1", "source_paragraphs": [2, 3], "quote": self.paragraphs[1]},
            {"evidence_id": "EV1", "source_paragraphs": [1, 2], "quote": self.paragraphs[1]},
        ):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                evidence.prepare_evidence_draft(self.paragraphs, [selection], source_ref="source.json")

    def test_ambiguous_quotes_rejected_before_candidate_freeze(self):
        for paragraph in ("站住！站住！", "站\n住！站住！"):
            with self.subTest(paragraph=paragraph), self.assertRaises(ValueError):
                evidence.prepare_evidence_draft([paragraph], [{"evidence_id": "EV1", "source_paragraphs": [1], "quote": "站住！"}], source_ref="source.json")

    def test_source_change_even_outside_selected_range_invalidates_catalog(self):
        draft = self.draft()
        changed = list(self.paragraphs)
        changed[3] = "随后留下。"
        with self.assertRaisesRegex(ValueError, "原文摘要已变化"):
            evidence.verify_evidence_draft(changed, draft)

    def test_tampering_and_approval_flags_are_not_accepted(self):
        for field, value in (("evidence", "守卫放过他。"), ("raw_evidence", "改写了原文。"),
                             ("raw_paragraphs", [{"id": 2, "text": "改写了原文。"}]), ("binding_sha256", "0" * 64)):
            draft = self.draft()
            draft["records"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                evidence.verify_evidence_draft(self.paragraphs, draft)
        for field, value in (("semantic_review", "passed"), ("formal_ready", True)):
            draft = self.draft()
            draft[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                evidence.verify_evidence_draft(self.paragraphs, draft)

    def test_old_ledger_or_decision_payload_is_never_a_preparation_input(self):
        with self.assertRaises(ValueError):
            evidence.prepare_evidence_draft(self.paragraphs, [{"evidence_id": "EV1", "source_paragraphs": [2],
                "reviewed_full_script": True, "expected_record_digest": "0" * 64}], source_ref="source.json")
        with self.assertRaises(ValueError):
            evidence.prepare_evidence_draft(self.paragraphs, self.selections * 2, source_ref="source.json")

    def test_check_is_readonly_and_returns_no_semantic_approval(self):
        draft = self.draft()
        before = copy.deepcopy(draft)
        report = evidence.verify_evidence_draft(self.paragraphs, draft)
        self.assertEqual(draft, before)
        self.assertEqual(report["semantic_review"], "not_performed")
        self.assertFalse(report["formal_ready"])
        fields = evidence.evidence_fields(self.paragraphs, draft, "EV001")
        fields["source_paragraphs"].clear()
        self.assertEqual(draft, before)

    def test_cli_prepares_new_file_checks_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, selections, output = (directory / name for name in ("source.json", "selections.json", "evidence.json"))
            source.write_text(json.dumps({"paragraphs": [{"id": i, "text": text} for i, text in enumerate(self.paragraphs, 1)]}, ensure_ascii=False), encoding="utf-8")
            selections.write_text(json.dumps(self.selections), encoding="utf-8")
            before = (source.read_bytes(), selections.read_bytes())
            command = [sys.executable, str(MODULE / "evidence_draft.py"), "prepare", str(source), str(selections), "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = output.read_bytes()
            self.assertEqual(json.loads(saved)["source_ref"], str(source.resolve()))
            checked = subprocess.run([sys.executable, str(MODULE / "evidence_draft.py"), "check", str(source), str(output)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(checked.returncode, 0, checked.stderr)
            repeated = subprocess.run(command, capture_output=True)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(output.read_bytes(), saved)
            self.assertEqual((source.read_bytes(), selections.read_bytes()), before)
            source_target = subprocess.run(command[:-1] + [str(source)], capture_output=True)
            self.assertNotEqual(source_target.returncode, 0)
            self.assertEqual(source.read_bytes(), before[0])


if __name__ == "__main__":
    unittest.main()
