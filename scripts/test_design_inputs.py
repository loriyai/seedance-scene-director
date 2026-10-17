"""Input-index regressions: derived facts stay read-only and reuse fails closed."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_asset_visual_design import SKILL, fixture, save
import design_inputs
import test_group_mask_design as masks


class DesignInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.script, self.source, self.paragraphs, self.data, self.payload = fixture(self.work)
        self.style = self.work / "video-style.txt"

    def prepare(self, ids=None, shared=None):
        return design_inputs.prepare(self.script, self.source, self.work / "ledger.json", self.style, ids, shared)

    def masked(self):
        return masks.GroupMaskDesignTests.masked(self)

    def test_all_inputs_project_without_mutation_or_semantic_approval(self):
        before = {p: p.read_bytes() for p in self.work.rglob('*') if p.is_file()}
        result = self.prepare()
        self.assertEqual(result['requested_asset_ids'], ['CHAR0001', 'PROP0001', 'SET0001'])
        row = next(r for r in result['items'] if r['asset_id'] == 'CHAR0001')
        self.assertEqual(row['facts'], self.data['characters'][0]['visual_facts'])
        self.assertEqual(result['semantic_review'], 'not_performed')
        self.assertFalse(result['formal_ready'])
        self.assertFalse(result['bindable'])
        self.assertEqual(design_inputs.check(result)['status'], 'current')
        self.assertEqual(before, {p: p.read_bytes() for p in self.work.rglob('*') if p.is_file()})

    def test_absorbed_candidate_and_base_interaction_are_not_lost_or_promoted(self):
        masks.GroupMaskDesignTests.linked_mask(self)
        candidate = {'id':'PC0001', 'name':'衣角', 'disposition':'character', 'target_id':'CHAR0001',
                     'source_paragraphs':[1], 'evidence':'拽衣角', 'reason':'服装必须支持抓握，不另建PROP'}
        self.data['prop_coverage_review'] = {'candidates':[candidate]}
        report = design_inputs.project_inputs(self.data, self.payload, ['CHAR0002'])
        variant = next(r for r in report['items'] if r['asset_id'] == 'CHAR0002')
        self.assertEqual(variant['absorbed_candidates'],
                         [{'candidate_id':'PC0001', 'inherited_from_base':True, 'record':candidate}])
        self.assertIn('interaction_requirements', variant['review_required'])
        self.assertNotIn('interaction_facts', variant)
        self.assertEqual(variant['inheritance']['prop_asset_id'], 'PROP0001')
        self.assertEqual(report['dependency_asset_ids'], ['CHAR0001','CHAR0002','PROP0001'])

    def test_scene_dependency_and_facts_point_to_current_location(self):
        report = self.prepare(['SET0001'])
        self.assertEqual(report['dependency_asset_ids'], ['LOC0001','SET0001'])
        self.assertEqual(report['items'][0]['inheritance']['location_id'], 'LOC0001')
        self.assertTrue(report['items'][0]['scene_refs'])

    def test_tampered_derived_index_rejected_even_after_rehash(self):
        index = self.prepare()
        index['items'][0]['facts'][0]['text'] = '红衣'
        index['binding_sha256'] = design_inputs.design.fingerprint({k:v for k,v in index.items() if k!='binding_sha256'})
        with self.assertRaisesRegex(ValueError, '索引内容被修改'):
            design_inputs.check(index)

    def test_every_input_change_invalidates_instead_of_refreshing_receipt(self):
        for name in ('source.json','ledger.json','script.txt','video-style.txt'):
            path = {'script.txt':self.script}.get(name,self.work/name)
            old = path.read_bytes()
            index = self.prepare()
            path.write_bytes(old+b' ')
            try:
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, '输入或规则已变化'):
                    design_inputs.check(index)
            finally:
                path.write_bytes(old)

    def test_shared_design_change_invalidates_index(self):
        shared = self.work/'shared.json'
        save(shared,self.payload)
        index = self.prepare(['CHAR0001'],shared)
        row = next(r for r in index['items'] if r['asset_id']=='CHAR0001')
        self.assertIsNotNone(row['shared_anchor_sha256'])
        self.payload['identity_anchors'][0]['body'] += '，肩部略宽'
        save(shared,self.payload)
        with self.assertRaisesRegex(ValueError,'输入或规则已变化'):
            design_inputs.check(index)

    def test_existing_ledger_design_anchors_remain_indexed(self):
        designed=design_inputs.design.apply_design(self.data,self.payload,self.style.read_text().strip())
        save(self.work/'ledger.json',designed)
        index=self.prepare(['CHAR0001'])
        self.assertIsNotNone(index['items'][0]['shared_anchor_sha256'])
        self.assertIsNotNone(index['items'][0]['shared_design_sha256'])
        self.assertEqual(design_inputs.check(index)['status'],'current')

    def test_rule_or_compiler_change_invalidates_index(self):
        index=self.prepare()
        altered={**index['rule_sha256'],'scripts/changed':'different'}
        with patch.object(design_inputs,'rule_hashes',return_value=altered):
            with self.assertRaisesRegex(ValueError,'输入或规则已变化'):
                design_inputs.check(index)

    def test_invalid_inventory_cannot_hide_behind_selected_character(self):
        self.data['props'][0]['visible_uses'].pop()
        save(self.work/'ledger.json',self.data)
        with self.assertRaisesRegex(ValueError,'至少需要2次'):
            self.prepare(['CHAR0001'])

    def test_unknown_duplicate_prompt_only_and_empty_scope_rejected(self):
        for ids in ([],['CHAR0001','CHAR0001'],['CHAR9999'],['LOC0001']):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                self.prepare(ids)

    def test_cli_refuses_overwrite_and_check_has_no_write_side_effect(self):
        out=self.work/'index.json'
        command=[sys.executable,'-B',str(SKILL/'modules/screenplay-standardizer/scripts/design_inputs.py')]
        args=['prepare',str(self.script),str(self.source),str(self.work/'ledger.json'),
              '--style-file',str(self.style),'--output',str(out)]
        first=subprocess.run(command+args,capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(first.returncode,0,first.stderr)
        content=out.read_bytes()
        repeat=subprocess.run(command+args,capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(repeat.returncode,0)
        self.assertIn('输出已存在',repeat.stderr)
        checked=subprocess.run(command+['check',str(out)],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(checked.returncode,0,checked.stderr)
        self.assertEqual(json.loads(checked.stdout)['status'],'current')
        self.assertEqual(content,out.read_bytes())


if __name__=='__main__':
    unittest.main()
