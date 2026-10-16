"""Explicit v2 animals retain species anatomy without changing ordinary characters."""
import tempfile
import unittest
from pathlib import Path
from test_asset_visual_design import fixture, save, ledger, design


class AnimalCharacterDesignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(Path(self.temp.name), with_prop=False)
        self.paragraphs[:] = ["一只瘦弱的小白猫蹲在门口。", "小白猫抬起头。"]
        self.data["source_digest"] = ledger.digest(self.paragraphs)
        self.data["characters"][0].update(name="小白猫-基础形象", source_entry="小白猫-基础形象", asset_type="animal", species="家猫",
            visual_facts=[{"text":"瘦弱白猫","evidence":"瘦弱的小白猫","source_paragraphs":[1],"scene_ids":["SC0001"],"attribution":"叙述"}])
        save(self.script, "\n\n".join(f"场景{i}-日-内-陈府\n人物：小白猫-基础形象；\n△ {p}" for i,p in enumerate(self.paragraphs,1))+"\n")
        save(self.source, {"paragraphs":[{"id":i,"text":p} for i,p in enumerate(self.paragraphs,1)]})
        self.payload["image_design_version"]="gpt-image-2-v2"
        self.payload["identity_anchors"]=[{"id":"白猫","age":"不强化年龄和性征的家猫","body":"瘦弱四足猫科体态，四爪和尾巴完整","face":"猫科口鼻、三角耳与自然猫眼","hair":"白色毛被，无人类发型"}]
        self.payload["designs"][0].update(identity_anchor_id="白猫",styling="不穿衣物，无额外配件",basis="按原文瘦弱白猫设计真实猫科结构")

    def compile(self):
        value=design.apply_design(self.data,self.payload,"3D国漫写实厚涂风格")
        ledger.validate(ledger.parse_script(self.script),self.paragraphs,value)
        return value

    def test_animal_board_has_real_anatomy_without_human_clothing_template(self):
        value=self.compile();item=value["characters"][0]
        self.assertEqual(item["image_format"]["subject_kind"],"animal")
        self.assertIn("不人化",item["image_prompt"])
        self.assertIn("四爪和尾巴完整",item["image_prompt"])
        self.assertNotIn("服装剪裁可信",item["image_prompt"])
        save(Path(self.temp.name)/"designed.json",value)
        ledger.render(self.script,self.source,Path(self.temp.name)/"designed.json",Path(self.temp.name)/"catalog.txt")

    def test_missing_species_rejected(self):
        self.data["characters"][0].pop("species")
        with self.assertRaises(ValueError):self.compile()

    def test_species_change_expires_design(self):
        value=self.compile();value["characters"][0]["species"]="犬"
        with self.assertRaises(ValueError):design.validate_visual_design(value)


if __name__ == "__main__":unittest.main()
