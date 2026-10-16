"""Explicit collection layouts preserve object counts without changing defaults."""
import copy
import tempfile
import unittest
from pathlib import Path
from test_asset_visual_design import fixture, design


class PropCollectionLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        _, _, _, self.data, self.payload = fixture(Path(self.temp.name))
        self.payload['image_design_version'] = 'gpt-image-2-v2'

    def test_explicit_collection_does_not_merge_objects_or_change_character(self):
        baseline = design.apply_design(self.data, self.payload, '3D国漫写实厚涂风格')
        prop = self.payload['designs'][-1]
        prop.update(product_layout='multiple_objects', layout_basis='源为两个独立物件，本图完整展示二者', structure='两个独立物件并排分开')
        actual = design.apply_design(self.data, self.payload, '3D国漫写实厚涂风格')
        item = actual['props'][0]
        self.assertEqual(item['image_format']['layout'], 'multiple_complete_objects')
        self.assertIn('两个独立物件', item['image_prompt'])
        self.assertNotIn('单件居中', item['image_prompt'])
        self.assertEqual(baseline['characters'], actual['characters'])
        self.assertEqual(baseline['scene_assets'], actual['scene_assets'])

    def test_no_name_or_quantity_heuristic_changes_default(self):
        self.data['props'][0]['name'] = '十个汤碗'
        actual = design.apply_design(self.data, self.payload, '3D国漫写实厚涂风格')
        self.assertEqual(actual['props'][0]['image_format']['layout'], 'single_complete_product')
        self.assertIn('单件居中', actual['props'][0]['image_prompt'])

    def test_requires_explicit_valid_v2_flag_and_basis(self):
        for version, flag, basis in [('gpt-image-2-v1', 'multiple_objects', '有依据'), ('gpt-image-2-v2', 'guess', '有依据'), ('gpt-image-2-v2', 'multiple_objects', '')]:
            with self.subTest(version=version, flag=flag, basis=basis):
                payload = copy.deepcopy(self.payload)
                payload['image_design_version'] = version
                payload['designs'][-1].update(product_layout=flag, layout_basis=basis)
                with self.assertRaises(ValueError):
                    design.apply_design(self.data, payload, '3D国漫写实厚涂风格')


if __name__ == '__main__':
    unittest.main()
