"""Regression checks for the append-only authoring-session-v5 boundary."""
import hashlib
import json
from pathlib import Path
import unittest

import authoring_session as session
import director
import storyboard_text as text_core
import test_storyboard_text_delivery as delivery_fixtures


class AuthoringSessionV5Test(unittest.TestCase):
    def setUp(self):
        fixture = delivery_fixtures.TextDeliveryIntegration("test_check_and_preview_are_read_only_for_session_and_history")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.fixture = fixture
        state = director.load(fixture.project)
        state["authoring_session_protocol"] = session.PROTOCOL
        director.write_json(fixture.project / "session.json", state)
        self.units = text_core.parse(fixture.draft.read_text(encoding="utf-8"), state)
        self.scope = [self.units[0]["span"][0], self.units[-1]["span"][1]]

    def start(self):
        return session.start(self.fixture.project, self.scope, "writer-fixture")

    def append_all(self):
        for unit in self.units:
            for index, shot in enumerate(unit["shots"], 1):
                session.append_event(
                    self.fixture.project,
                    self.fixture.draft,
                    f'{unit["draft_key"]}#{index}',
                    hashlib.sha256(shot["text"].encode("utf-8")).hexdigest(),
                    list(unit["source_records"]),
                )

    def test_unbound_project_is_structured(self):
        with self.assertRaises(session.AuthoringSessionError) as raised:
            session.start(self.fixture.project / "missing", self.scope, "writer")
        self.assertEqual(raised.exception.code, "PROJECT_UNBOUND")

    def test_start_does_not_create_or_change_draft(self):
        before = self.fixture.draft.read_bytes()
        result = self.start()
        self.assertEqual(result["protocol"], session.PROTOCOL)
        self.assertEqual(self.fixture.draft.read_bytes(), before)
        self.assertTrue(session.session_path(self.fixture.project).is_file())

    def test_duplicate_active_start_is_rejected(self):
        self.start()
        with self.assertRaises(session.AuthoringSessionError) as raised:
            self.start()
        self.assertEqual(raised.exception.code, "AUTHORING_SESSION_ACTIVE")

    def test_events_form_version_chain_without_rewriting_draft(self):
        self.start()
        before = self.fixture.draft.read_bytes()
        session.append_event(self.fixture.project, self.fixture.draft, "SC0001/u01#1",
                             "a" * 64, [self.units[0]["source_records"][0]])
        first = hashlib.sha256(before).hexdigest()
        self.fixture.draft.write_bytes(before + b" ")
        second = hashlib.sha256(self.fixture.draft.read_bytes()).hexdigest()
        for unit_index, unit in enumerate(self.units):
            for shot_index, shot in enumerate(unit["shots"], 1):
                if unit_index == 0 and shot_index == 1:
                    continue
                session.append_event(
                    self.fixture.project,
                    self.fixture.draft,
                    f'{unit["draft_key"]}#{shot_index}',
                    ("b" if unit_index == 0 else "c") * 64,
                    list(unit["source_records"]),
                )
        current = director.read_json(session.session_path(self.fixture.project))
        self.assertEqual(current["authoring_event_chain"][0]["draft_sha256"], first)
        self.assertEqual(current["authoring_event_chain"][-1]["draft_sha256"], second)
        self.assertEqual(current["authoring_event_chain"][1]["parent_draft_sha256"], first)
        report = session.validate_for_delivery(
            self.fixture.project, self.fixture.draft.read_bytes(), self.units, self.scope)
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertEqual(self.fixture.draft.read_bytes(), before + b" ")

    def test_missing_coverage_and_tampered_chain_block(self):
        self.start()
        self.append_all()
        report = session.validate_for_delivery(
            self.fixture.project, self.fixture.draft.read_bytes(), self.units, self.scope)
        self.assertEqual(report["status"], "passed", report["errors"])
        path = session.session_path(self.fixture.project)
        value = director.read_json(path)
        value["authoring_event_chain"][0]["shot_key"] = "SC0001/u99#1"
        director.write_json(path, value)
        report = session.validate_for_delivery(
            self.fixture.project, self.fixture.draft.read_bytes(), self.units, self.scope)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("AUTHORING_CHAIN_BROKEN", {row["code"] for row in report["errors"]})

    def test_provenance_trace_must_match_current_session(self):
        self.start()
        self.append_all()
        current = director.read_json(session.session_path(self.fixture.project))
        trace = {
            "authoring_session_protocol": session.PROTOCOL,
            "provenance_manifest": current["provenance_manifest"],
            "authoring_event_chain": current["authoring_event_chain"],
            "authoring_event_chain_sha256": current["authoring_event_chain_sha256"],
        }
        report = session.validate_for_delivery(
            self.fixture.project, self.fixture.draft.read_bytes(), self.units, self.scope, trace=trace)
        self.assertEqual(report["status"], "passed", report["errors"])
        trace["authoring_event_chain_sha256"] = "0" * 64
        report = session.validate_for_delivery(
            self.fixture.project, self.fixture.draft.read_bytes(), self.units, self.scope, trace=trace)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("AUTHORING_CHAIN_MISMATCH", {row["code"] for row in report["errors"]})

    def test_close_marks_session_closed(self):
        self.start()
        self.append_all()
        result = session.close(self.fixture.project,
                               hashlib.sha256(self.fixture.draft.read_bytes()).hexdigest())
        self.assertEqual(result["status"], "closed")
        with self.assertRaises(session.AuthoringSessionError) as raised:
            session.active(self.fixture.project)
        self.assertEqual(raised.exception.code, "AUTHORING_SESSION_CLOSED")


if __name__ == "__main__":
    unittest.main()
