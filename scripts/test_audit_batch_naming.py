"""Batch identity, crash recovery and prompt-only character regressions; offline."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import batch_naming as bn
import canvas as c
import director as d
import test_audit_director as fixture


class BatchNamingChecks(unittest.TestCase):
    unit = fixture.AuditDirectorChecks.unit
    request = fixture.AuditDirectorChecks.request

    def setUp(self):
        fixture.AuditDirectorChecks.setUp(self)
        self.pristine = copy.deepcopy(self.state)

    def submit(self, request, *, state=None, project=None, scope=None):
        state = self.state if state is None else state
        project = self.project if project is None else project
        path = project / 'request.json'
        d.write_json(path, request)
        from test_review_support import approve_fixture
        args = SimpleNamespace(project=project, request=path,
                        output=project / 'outputs' / (request['request_id'] + '.txt'), batch_scope=scope)
        return d.commit(approve_fixture(args, state), state)

    def repeated(self, key):
        request = dict(self.request(), request_id=key)
        request['range_confirmation'] = d.range_review(self.state, request['span'])['confirmation_token']
        return request

    def project_at(self, path):
        path.mkdir(parents=True)
        (path / 'video-style.txt').write_text('3D国漫\n', encoding='utf-8')
        state = copy.deepcopy(self.pristine)
        d.write_json(path / 'session.json', state)
        return state

    def test_next_range_resets_visible_scene_counter_and_keeps_stable_ids(self):
        first = self.submit(self.request())
        self.assertEqual(first['names'], ['A01-场景1-分镜1', 'A02-场景1-分镜2'])
        second = self.submit(self.repeated('again'))
        self.assertEqual(second['names'], ['B01-场景1-分镜1', 'B02-场景1-分镜2'])
        self.assertEqual(second['ids'], ['场景1-分镜3', '场景1-分镜4'])
        self.assertEqual(self.state['next_numbers'], {'SC0001': 5})
        text = Path(second['output']).read_text(encoding='utf-8')
        self.assertTrue(text.startswith('B01-场景1-分镜1（时长：4秒）\n'))
        self.assertNotIn('01 - ', text)
        self.assertNotIn('@黑幕', text)
        self.assertEqual(len(d.read_json(self.project / bn.REGISTRY)['entries']), 2)

    def test_standard_chat_directory_shares_letters_across_projects(self):
        scope = self.project / 'work' / 'seedance-scene-director'
        one, two = scope / 'one', scope / 'two'
        state1, state2 = self.project_at(one), self.project_at(two)
        self.assertEqual(self.submit(self.request(), state=state1, project=one)['names'][0], 'A01-场景1-分镜1')
        self.assertEqual(self.submit(self.request(), state=state2, project=two)['names'][0], 'B01-场景1-分镜1')
        self.assertEqual(state1['batch_scope'], str(scope.resolve()))
        self.assertEqual(state2['batch_scope'], str(scope.resolve()))
        self.assertFalse((self.project.parent / bn.REGISTRY).exists())

    def test_regenerated_scene_counts_and_canvas_keys_are_independent(self):
        self.state['records'] = [dict(self.state['records'][0], id=f'L{i + 1:06d}',
                                     start=i * 9, end=(i + 1) * 9,
                                     scene='SC0001' if i == 0 else 'SC0002') for i in range(10)]
        self.state['length'] = 90
        request = {'mode': 'append', 'request_id': 'first', 'span': [0, 90],
                   'units': [self.unit([i * 9, (i + 1) * 9], 'SC0001' if i == 0 else 'SC0002')
                             for i in range(10)]}
        self.submit(request)
        old_receipt = copy.deepcopy(self.state['receipts'][0])
        request = dict(request, request_id='regenerated',
                       range_confirmation=d.range_review(self.state, [0, 90])['confirmation_token'])
        result = self.submit(request)
        self.assertEqual(result['names'][:2], ['B01-场景1-分镜1', 'B02-场景2-分镜1'])
        self.assertEqual(result['names'][-1], 'B10-场景2-分镜9')
        self.assertEqual(result['ids'][:2], ['场景1-分镜2', '场景2-分镜10'])
        units = self.state['receipts'][-1]['units']
        self.assertEqual([u['batch_index'] for u in units], list(range(1, 11)))
        self.assertEqual(units[1]['predecessor'], units[0]['id'])
        self.assertEqual(self.state['receipts'][0], old_receipt)
        before = copy.deepcopy(self.state)
        retry = self.submit(request)
        self.assertEqual(retry['status'], 'already_committed')
        self.assertEqual((retry['names'], retry['ids']), (result['names'], result['ids']))
        self.assertEqual(self.state, before)
        for unit in units:
            for selector in (unit['id'], unit['display_name'], unit['display_name'].split('-', 1)[0]):
                self.assertEqual(d.resolve_unit_id(self.state, selector), unit['id'])
                self.assertEqual(c.selected_units(self.state, ids=[selector])[0]['id'], unit['id'])
        # The offline Canvas plan keeps mappings keyed by historical stable IDs.
        canvas_state = {'mappings': {u['id']: {'node_id': f'existing-{i}'} for i, u in enumerate(units)}}
        mappings = copy.deepcopy(canvas_state)
        planned, pending = c.desired_videos(self.state, canvas_state, {}, units)
        self.assertEqual(pending, [])
        self.assertEqual([u['key'] for u in planned], result['ids'])
        self.assertEqual([u['name'] for u in planned], result['names'])
        self.assertEqual(canvas_state, mappings)
        for item, unit in zip(planned, units):
            self.assertEqual(item['reference_keys'], sorted(c.unit_asset_ids(self.state, unit)))

    def test_replace_new_batch_keeps_display_name_stable_id_and_original_receipt(self):
        self.submit(self.request())
        self.submit(self.repeated('again'))
        old_receipt = copy.deepcopy(self.state['receipts'][-1])
        old = copy.deepcopy(self.state['units'][-1])
        replacement = dict(old, id='B02-场景1-分镜2', display_name='FORGED', batch_index=999)
        result = self.submit({'mode': 'replace', 'request_id': 'edit-b', 'units': [replacement]})
        self.assertEqual(result['ids'], ['场景1-分镜4'])
        self.assertEqual(result['names'], ['B02-场景1-分镜2'])
        current = self.state['units'][-1]
        self.assertEqual((current['batch_label'], current['batch_index'], current['predecessor']),
                         ('B', 2, old['predecessor']))
        self.assertEqual(current['previous_versions'][-1]['display_name'], old['display_name'])
        self.assertEqual(self.state['receipts'][1], old_receipt)
        self.assertEqual(self.state['next_numbers'], {'SC0001': 5})

    def test_old_named_receipt_replays_and_new_batch_uses_scene_local_names(self):
        self.state['next_numbers'] = {'SC0001': 7}
        request = self.request()
        self.submit(request)
        # Emulate a pre-fix receipt whose display suffix copied historical IDs.
        for unit in self.state['units'] + self.state['receipts'][0]['units']:
            unit['display_name'] = f"{unit['batch_label']}{unit['batch_index']:02d}-{unit['id']}"
        d.save(self.project, self.state)
        old = copy.deepcopy(self.state['receipts'][0])
        old_text = '\n\n'.join(d.render_unit(self.state, u) for u in old['units']) + '\n'
        result = self.submit(request)
        self.assertEqual(result['names'], ['A01-场景1-分镜7', 'A02-场景1-分镜8'])
        self.assertEqual(Path(result['output']).read_text(encoding='utf-8'), old_text)
        self.assertEqual(self.state['receipts'][0], old)
        next_result = self.submit(self.repeated('new-version'))
        self.assertEqual(next_result['names'], ['B01-场景1-分镜1', 'B02-场景1-分镜2'])
        self.assertEqual(next_result['ids'], ['场景1-分镜9', '场景1-分镜10'])

    def test_custom_projects_share_only_an_explicit_scope(self):
        one, two = self.project / 'custom-one', self.project / 'custom-two'
        state1, state2 = self.project_at(one), self.project_at(two)
        scope = self.project / 'chat-batches'
        self.assertTrue(self.submit(self.request(), state=state1, project=one, scope=scope)['names'][0].startswith('A01-'))
        self.assertTrue(self.submit(self.request(), state=state2, project=two, scope=scope)['names'][0].startswith('B01-'))
        self.assertEqual(bn.scope_for(self.project), self.project.resolve())
        with self.assertRaisesRegex(ValueError, '不能静默切换'):
            bn.scope_for(one, state1['batch_scope'], self.project / 'other-chat')

    def test_z_rolls_over_to_aa_and_new_chats_restart_a(self):
        for number in range(1, 28):
            result = self.submit(self.repeated('batch-' + str(number)))
        self.assertEqual(result['names'][0], 'AA01-场景1-分镜1')
        self.assertEqual(result['ids'][0], '场景1-分镜53')
        self.assertEqual([bn.letter(n) for n in (1, 26, 27, 52, 53, 702, 703)],
                         ['A', 'Z', 'AA', 'AZ', 'BA', 'ZZ', 'AAA'])
        other = self.project / 'new-chat' / 'work' / 'seedance-scene-director' / 'project'
        state = self.project_at(other)
        self.assertTrue(self.submit(self.request(), state=state, project=other)['names'][0].startswith('A01-'))

    def test_numbers_cross_scenes_and_do_not_truncate_above_99(self):
        records = []
        for index in range(102):
            records.append(dict(self.state['records'][0], id=f'L{index + 1:06d}',
                                start=index * 9, end=(index + 1) * 9,
                                scene='SC0001' if index < 100 else 'SC0002'))
        self.state.update(records=records, length=918)
        request = {'mode': 'append', 'request_id': 'large', 'span': [0, 918],
                   'units': [self.unit([i * 9, (i + 1) * 9], 'SC0001' if i < 100 else 'SC0002') for i in range(102)]}
        result = self.submit(request)
        self.assertEqual(result['names'][98:], ['A99-场景1-分镜99', 'A100-场景1-分镜100',
                                               'A101-场景2-分镜1', 'A102-场景2-分镜2'])
        self.assertIn('A100-场景1-分镜100（时长：4秒）', Path(result['output']).read_text(encoding='utf-8'))
        request = dict(request, request_id='large-again',
                       range_confirmation=d.range_review(self.state, request['span'])['confirmation_token'])
        second = self.submit(request)
        self.assertEqual(second['names'][99:], ['B100-场景1-分镜100', 'B101-场景2-分镜1', 'B102-场景2-分镜2'])
        self.assertEqual(second['ids'][99:], ['场景1-分镜200', '场景2-分镜3', '场景2-分镜4'])
        self.assertEqual(d.resolve_unit_id(self.state, 'B100'), '场景1-分镜200')

    def test_replace_alias_keeps_original_name_and_does_not_consume_a_letter(self):
        first = self.submit(self.request())
        original = copy.deepcopy(self.state['receipts'][0])
        replacement = copy.deepcopy(self.state['units'][1])
        replacement.update(id='A02', display_name='FORGED', batch_label='Z', batch_index=99)
        edit_request = {'mode': 'replace', 'request_id': 'edit', 'units': [replacement]}
        result = self.submit(edit_request)
        self.assertEqual(result['ids'], ['场景1-分镜2'])
        self.assertEqual(result['names'], ['A02-场景1-分镜2'])
        self.assertEqual(self.state['units'][1]['batch_index'], 2)
        self.assertEqual(self.state['receipts'][0], original)
        self.assertEqual(self.submit(edit_request)['status'], 'already_committed')
        self.assertEqual(self.state['units'][1]['version'], 2)
        self.assertEqual(d.resolve_unit_id(self.state, 'A02-场景1-分镜2'), '场景1-分镜2')
        self.assertEqual(d.resolve_unit_id(self.state, '场景1-分镜2'), '场景1-分镜2')
        for invalid in ('A2', 'A99', 'A02-场景1-分镜999'):
            with self.assertRaises(ValueError):
                d.resolve_unit_id(self.state, invalid)
        self.assertEqual(self.submit(self.repeated('next'))['names'][0], 'B01-场景1-分镜1')
        self.assertTrue(Path(first['output']).read_text(encoding='utf-8').startswith('A01-'))

    def test_failed_check_and_failed_atomic_save_do_not_consume_a_letter(self):
        invalid = self.request()
        invalid['units'][0]['duration'] = 0
        with self.assertRaises(ValueError):
            self.submit(invalid)
        self.assertFalse((self.project / bn.REGISTRY).exists())
        before = copy.deepcopy(self.state)
        with patch.object(d, 'save', side_effect=OSError('simulated failed atomic save')):
            with self.assertRaises(OSError):
                self.submit(self.request())
        self.assertEqual(self.state, before)
        self.assertEqual(d.read_json(self.project / bn.REGISTRY)['entries'], {})
        self.assertTrue(self.submit(self.request())['names'][0].startswith('A01-'))

    def test_failed_delivery_recovers_same_receipt_without_renumbering(self):
        with patch.object(d, 'render_receipt', side_effect=OSError('simulated output failure')):
            with self.assertRaises(OSError):
                self.submit(self.request())
        self.assertEqual(self.state['units'][0]['display_name'], 'A01-场景1-分镜1')
        retry = self.submit(self.request())
        self.assertEqual(retry['status'], 'already_committed')
        self.assertEqual(retry['names'], ['A01-场景1-分镜1', 'A02-场景1-分镜2'])
        self.assertEqual(len(self.state['ranges']), 1)
        self.assertEqual(len(d.read_json(self.project / bn.REGISTRY)['entries']), 1)

    def test_interrupted_registry_finalization_recovers_from_session(self):
        real_write = bn._write
        calls = 0
        def crash_after_session(path, value):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('simulated final registry write failure')
            return real_write(path, value)
        with patch.object(bn, '_write', side_effect=crash_after_session):
            with self.assertRaises(OSError):
                self.submit(self.request())
        entry = next(iter(d.read_json(self.project / bn.REGISTRY)['entries'].values()))
        self.assertEqual(entry['status'], 'prepared')
        retry = self.submit(self.request())
        self.assertEqual(retry['status'], 'already_committed')
        self.assertEqual(next(iter(d.read_json(self.project / bn.REGISTRY)['entries'].values()))['status'], 'committed')
        self.assertTrue(self.submit(self.repeated('next'))['names'][0].startswith('B01-'))

    def test_output_cannot_overwrite_shared_registry_or_lock(self):
        scope = self.project / 'shared-chat'
        for folder in (self.project, scope):
            for filename in (bn.REGISTRY, '.batch-names.lock'):
                with self.subTest(folder=folder, filename=filename), self.assertRaisesRegex(ValueError, '交付路径'):
                    d.validate_output_path(self.state, self.project, folder / filename,
                                           scope if folder == scope else None)

    def test_pre_session_interruption_releases_unused_letter(self):
        value = {'schema_version': 1, 'entries': {bn._key(self.project, 'interrupted'): {
            'number': 1, 'label': 'A', 'status': 'prepared', 'project': str(self.project.resolve()),
            'request_id': 'interrupted', 'request_hash': 'fake'}}}
        d.write_json(self.project / bn.REGISTRY, value)
        self.assertTrue(self.submit(self.request())['names'][0].startswith('A01-'))
        self.assertEqual(len(d.read_json(self.project / bn.REGISTRY)['entries']), 1)

    def test_scope_lock_rejects_concurrent_allocation_without_extra_number(self):
        with bn.allocate(self.project, self.project, 'outer', 'hash') as label:
            self.assertEqual(label, 'A')
            with self.assertRaisesRegex(ValueError, '正在提交'):
                with bn.allocate(self.project, self.project, 'inner', 'hash'):
                    self.fail('concurrent scope acquired')
        self.assertEqual(d.read_json(self.project / bn.REGISTRY)['entries'], {})

    def test_legacy_black_line_and_new_edge_only_mode_render_separately(self):
        unit = dict(self.unit([0, 9]), id='场景1-分镜1', style='3D国漫')
        old_text = d.render_unit(self.state, unit)
        self.assertEqual(old_text.splitlines()[1], '@黑幕')
        unit.update(display_name='A01-场景1-分镜1', black_reference_mode=d.BLACK_REFERENCE_MODE)
        text = d.render_unit(self.state, unit, lambda value: value.replace('@黑幕', '<BLACK>'))
        self.assertTrue(text.startswith('A01-场景1-分镜1（时长：4秒）\n'))
        self.assertNotIn('@黑幕', text)
        self.assertNotIn('<BLACK>', text)

    def test_prompt_only_character_remains_a_subject_and_renders_without_asset_tags(self):
        self.state['ledger']['characters'][0].update(name='村民-群像-村口', visual_mode='prompt_only')
        unit = dict(self.unit([0, 9]), id='场景1-分镜1', style='3D国漫')
        unit['shots'][0].update(camera='侧面缓推，跟随@{CHAR0001}',
                                composition='@村民-群像-村口 站在前景', text='@{CHAR0001}一齐转身。')
        d.validate_unit(self.state, unit, unit['span'], require_continuity=True)
        text = d.render_unit(self.state, unit)
        self.assertIn('人物：村民-群像-村口；', text)
        self.assertIn('（村民-群像-村口的中景；', text)
        self.assertIn('村民-群像-村口一齐转身。', text)
        self.assertNotIn('@村民', text)
        self.assertEqual(d.asset_text(self.state, unit, '@{CHAR0001}站着。')[1], {'CHAR0001'})
        unit['shots'][0]['focus'] = []
        with self.assertRaisesRegex(ValueError, '观看焦点'):
            d.validate_unit(self.state, unit, unit['span'])

    def test_prompt_only_voice_keeps_exact_dialogue_without_at_speaker(self):
        self.state['ledger']['characters'][0].update(name='村民-群像-村口', visual_mode='prompt_only')
        unit = dict(self.unit([0, 9]), id='场景1-分镜1', style='3D国漫')
        unit['audio'] = [{'record': 'VOICE1', 'character_id': 'CHAR0001', 'voice': 'OS',
                          'text': '大家看这里！', 'speed': '常速'}]
        unit['shots'][0]['dialogue_parts'] = [{'record': 'VOICE1', 'text': '大家看这里！', 'delivery': {'volume': '提高音量'}}]
        text = d.render_unit(self.state, unit)
        self.assertIn('村民-群像-村口OS', text)
        self.assertIn('：“大家看这里！”', text)
        self.assertNotIn('@村民', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
