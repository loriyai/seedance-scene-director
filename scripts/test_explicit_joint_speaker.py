"""Known explicit joint labels pass; single speakers and action prose are preserved."""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "modules/screenplay-standardizer/scripts"))
import screenplay_review as review


class ExplicitJointSpeakerTests(unittest.TestCase):
    names = ["邱柏野", "谢奕辉", "卢筱筱"]

    def test_joint_known_names_with_bounded_cue(self):
        self.assertEqual(review.explicit_label("邱柏野、谢奕辉（齐声）：「学姐好。」", self.names),
                         {"speaker": "邱柏野、谢奕辉", "cue": "齐声", "text": "「学姐好。」"})

    def test_does_not_classify_unknown_duplicate_or_action_prose(self):
        for text in ["邱柏野、陌生人：你好。", "邱柏野、邱柏野：你好。", "邱柏野、谢奕辉站在门口。", "邱柏野看向谢奕辉：你好。"]:
            with self.subTest(text=text): self.assertIsNone(review.explicit_label(text, self.names))

    def test_single_speaker_unchanged(self):
        for text, expected in [
            ("卢筱筱：是的。", {"speaker": "卢筱筱", "cue": "", "text": "是的。"}),
            ("卢筱筱（低声）：是的。", {"speaker": "卢筱筱", "cue": "低声", "text": "是的。"}),
            ("卢筱筱「是的。」", {"speaker": "卢筱筱", "cue": "", "text": "「是的。」"})]:
            with self.subTest(text=text): self.assertEqual(review.explicit_label(text, self.names), expected)

    def test_joint_dialogue_coverage_and_speaker_pass_without_discarding_text(self):
        paragraphs = ["邱柏野、谢奕辉：「学姐好。」"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scene_001_0001-0001.txt"
            path.write_text("场景1-日-外-教学楼门口\n人物：邱柏野-基础形象；谢奕辉-基础形象；\n邱柏野、谢奕辉：学姐好。\n", encoding="utf-8")
            result = review.check_scene(paragraphs, 1, 1, path, {})
            self.assertEqual(result["coverage_missing"], [])
            self.assertEqual(result["coverage_extra"], [])
            self.assertTrue(result["speaker_items"])
            self.assertTrue(all(not item["issue"] for item in result["speaker_items"]))


if __name__ == "__main__": unittest.main()
