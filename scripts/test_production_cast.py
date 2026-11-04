"""Synthetic production casting only; never approves production semantics."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import director as d
import production_cast as casting
import storyboard_text as core
import storyboard_text_delivery as delivery
from storyboard_context import scene_context
import test_continuity_props as fixtures


class ProductionCastTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.CarryReferenceTests('runTest')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.project, self.draft = self.fx.project, self.fx.draft
        self.state = d.load(self.project)
        self.record = next(r for r in self.state['records'] if r['id'] == 'L000008')
        self.auth = self.project/'casting-authorization.json'
        self.auth_data = {'status': 'user_explicitly_approved',
            'authorized_operations': ['production_cast', 'supplemental_character_assets'],
            'items': [{'scene_ids': ['SC0002'], 'source_records': ['L000008'],
                       'span': [self.record['start'], self.record['end']]}]}
        d.write_json(self.auth, self.auth_data)
        self.row = {'scene_id': 'SC0002', 'actor_key': 'synthetic-body',
            'source_asset_id': 'CHAR0001', 'asset_id': 'CHAR9998',
            'source_records': ['L000008'], 'evidence': [{'record_id': 'L000008', 'quote': self.record['text']}],
            'basis': 'Synthetic fixture; no production role assignment.',
            'authorization': {'path': str(self.auth), 'sha256': d.file_hash(self.auth)},
            'asset': {'id': 'CHAR9998', 'name': '测试制作执行者-基础形象', 'source_entry': '陈默-基础形象',
                      'visual_mode': 'image_asset', 'visual_design': {'identity_anchor_id': 'fixture-only'},
                      'image_prompt': '虚构16:9人物参考图测试文字。', 'image_format': {'aspect_ratio': '16:9'}}}
        self.fx.fx.decisions_data['production_cast'] = [self.row]
        first, second = self.draft.read_text('utf-8').split('## SC0002 / u04', 1)
        self.draft.write_text(first+'## SC0002 / u04'+second.replace('CHAR0001','CHAR9998'), encoding='utf-8')

    def test_normal_entry_context_bind_preview_commit_validate_render_frozen_retry(self):
        self.fx.bind()
        context = scene_context(self.project, 'SC0002', authoring_trace=self.fx.trace)
        self.assertIn('CHAR9998', context['allowed_asset_ids']['characters'])
        state, units, info = self.fx.prepare()
        self.assertEqual(info['authoring_gate']['status'], 'passed')
        core.validate(state, units)
        self.assertEqual(delivery.check_incremental(self.project, self.draft, self.fx.span, self.fx.trace)['status'], 'incremental_checked')
        preview = delivery.preview(self.project, self.draft, span=self.fx.span, authoring_trace=self.fx.trace)
        self.assertFalse(preview['preview_reused'])
        self.assertTrue(delivery.preview(self.project, self.draft, span=self.fx.span, authoring_trace=self.fx.trace)['preview_reused'])
        rendered = delivery.render_units(state, units, preview=True, authoring_gate=info['authoring_gate'])
        self.assertIn('陈默说：“', rendered)
        self.assertNotIn('@测试制作执行者-基础形象 说', rendered)
        self.assertEqual(self.fx.commit_fixture('casting-test')['status'], 'committed')
        state = d.load(self.project)
        self.assertNotIn('CHAR9998', {a['id'] for a in state['ledger']['characters']})
        receipt = state['receipts'][-1]
        core.validate(state, receipt['units'])
        original = (self.project/'delivered.txt').read_bytes()
        self.auth.unlink()
        (self.project/'delivered.txt').unlink()
        with patch.object(delivery, 'prepare', side_effect=AssertionError('frozen receipt only')):
            result = delivery.commit(self.project, self.draft, 'casting-test', self.project/'missing.json',
                    self.project/'delivered.txt', self.fx.span, authoring_trace=self.fx.trace)
        self.assertEqual(result['status'], 'already_committed')
        self.assertEqual((self.project/'delivered.txt').read_bytes(), original)

    def test_scope_operation_evidence_and_source_slot_rejected(self):
        unit = {'scene_ids': ['SC0002'], 'source_records': ['L000008']}
        for field, value in [('source_asset_id','CHAR9999'), ('sample_id','missing'),
                ('source_records',['L000007']), ('scene_id','SC0001')]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    casting.validate(self.state, [unit], [{**self.row, field:value}])
        self.auth_data['authorized_operations'] = ['production_cast']
        d.write_json(self.auth, self.auth_data)
        self.row['authorization']['sha256'] = d.file_hash(self.auth)
        with self.assertRaisesRegex(ValueError, '操作类型'):
            casting.validate(self.state,[unit],[self.row])

    def test_candidate_cannot_borrow_unselected_quote_or_override_designed_asset(self):
        record = self.record
        clipped = core._slice_state(self.state,[record['start'],record['end']-1])
        with self.assertRaisesRegex(ValueError,'实际选区'):
            casting.validate(clipped,[{'source_records':['L000008']}],[self.row])
        row = copy.deepcopy(self.row)
        row['asset_id'] = 'CHAR0001'
        row['asset']['id'] = 'CHAR0001'
        with self.assertRaises(ValueError):
            casting.context(self.state,[row])

    def test_changed_mapping_requires_new_gate_and_cannot_masquerade_as_history(self):
        self.fx.bind()
        state, units, info = self.fx.prepare()
        altered = copy.deepcopy(units[1])
        altered['production_cast'][0]['basis'] = 'Unreviewed changed mapping.'
        with self.assertRaisesRegex(ValueError,'制作人物对应'):
            core.render_unit(state,altered,authoring_gate=info['authoring_gate'])
        self.row['basis'] = 'Changed candidate.'
        self.fx.bind('changed-cast.json')
        _,_,changed = self.fx.prepare()
        self.assertNotEqual(info['authoring_gate']['gate_digest'],changed['authoring_gate']['gate_digest'])

    def test_explicit_sample_selection_and_stable_body_validation(self):
        state = copy.deepcopy(self.state)
        asset = state['ledger']['characters'][0]
        asset['visual_design'] = {'group_members':[{'sample_id':'a','age':'adult','body':'tall','face':'long','hair':'black'},
                                                    {'sample_id':'b','age':'adult','body':'short','face':'round','hair':'black'}]}
        row = {k:v for k,v in self.row.items() if k!='asset'}
        row['asset_id'] = asset['id']
        unit = {'source_records':['L000008']}
        with self.assertRaisesRegex(ValueError,'单个样本'):
            casting.validate(state,[unit],[row])
        row['sample_id']='a'
        self.assertEqual(casting.validate(state,[unit],[row])[0]['sample_id'],'a')

    def test_missing_prompt_does_not_allow_overwriting_existing_design_or_appearance(self):
        state = copy.deepcopy(self.state)
        old = state['ledger']['characters'][0]
        old.pop('image_prompt', None)
        old['visual_design'] = {'group_members':[{'sample_id':'a'}, {'sample_id':'b'}]}
        old['appearance'] = {'identity':'original-person','incarnation':'original-body'}
        row = copy.deepcopy(self.row)
        row['asset_id'] = old['id']
        row['asset'].update(id=old['id'], name=old['name'], source_entry=old.get('source_entry',old['name']))
        with self.assertRaisesRegex(ValueError,'只允许补充'):
            casting.context(state,[row])
        # Exercise the same rejection through the real public CLI with files.
        d.write_json(self.project/'session.json', state)
        trace = self.project/'overwrite-designed-without-prompt.json'
        d.write_json(trace, {'policy':'shot-by-shot-v1','version':4,'production_cast':[row]})
        result = subprocess.run([sys.executable,'-B','-X','utf8',str(Path(delivery.__file__)),
            'context','--project',str(self.project),'--scene','SC0002','--authoring-trace',str(trace)],
            capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('只允许补充',json.loads(result.stdout)['message'])
        row['asset']['visual_design'] = copy.deepcopy(old['visual_design'])
        row['asset']['appearance'] = {'identity':'different-person','incarnation':'original-body'}
        with self.assertRaisesRegex(ValueError,'只允许补充'):
            casting.context(state,[row])
        row['asset']['appearance'] = copy.deepcopy(old['appearance'])
        supplemented = casting.context(state,[row])
        self.assertEqual(supplemented['ledger']['characters'][0]['visual_design'],old['visual_design'])
        self.assertTrue(supplemented['ledger']['characters'][0]['image_prompt'])

    def test_legacy_sidecars_have_no_new_digest_fields_and_v3_cannot_declare(self):
        self.assertEqual(casting.declarations({'version':3}),[])
        with self.assertRaises(ValueError):
            casting.declarations({'version':3,'production_cast':[]})


if __name__=='__main__':
    unittest.main()
