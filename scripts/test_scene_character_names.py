"""Scene-local clothing names through normal standardization and asset entry points."""
import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / 'modules/screenplay-standardizer/scripts'))
import screenplay_io as script_io
import screenplay_review as review
import asset_ledger as ledger
import asset_visual_design as design
import scene_character_names as names


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')


class SceneCharacterNames(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, dialogue='阿明，叫阿明-黑衣来！「别改。」', *, legacy=False):
        paragraphs = ['人物：阿明-黑衣；阿明月-基础形象；', '阿明看向阿明月。', '阿明：“' + dialogue + '”']
        source = self.work / 'original.txt'
        save(source, '\n'.join(paragraphs))
        with contextlib.redirect_stdout(io.StringIO()):
            script_io.prepare(source, self.work / 'project')
        self.project = self.work / 'project'
        if legacy:
            settings = json.loads((self.project / 'source.json').read_text(encoding='utf-8'))
            settings.pop('character_name_policy')
            save(self.project / 'source.json', settings)
        self.draft = self.project / 'scenes/scene_001_0001-0003.txt'
        save(self.draft, '场景1-日-内-房间\n人物：阿明-黑衣；阿明月-基础形象；\n'
             '△ 阿明-黑衣看向阿明月-基础形象。\n阿明-黑衣：' + dialogue + '\n')
        return paragraphs

    def test_review_assemble_init_validate_keep_dialogue_exact(self):
        paragraphs = self.fixture()
        report = review.make_report(self.project, {})
        self.assertEqual(report['status'], 'passed', report)
        save(self.project / 'review.json', report)
        output = self.project / 'script.txt'
        with contextlib.redirect_stdout(io.StringIO()):
            script_io.assemble(self.project, output, self.project / 'review.json')
            ledger.initialize(output, self.project, self.project / 'assets.json')
        data = json.loads((self.project / 'assets.json').read_text(encoding='utf-8'))
        first = data['characters'][0]
        self.assertEqual(first['name'], '阿明-黑衣')
        self.assertEqual(first['source_speaker'], '阿明-黑衣')
        self.assertEqual(first['scene_clothing'], {'base_name': '阿明'})
        for scene in data['scenes']:
            scene.update(scene_asset_id='SET0001', time_basis={'kind': '格式默认', 'source_paragraphs': []})
        data['locations'][0].update(identity_status='confirmed', identity_basis='本场唯一房间')
        data['scene_assets'] = [{'id': 'SET0001', 'name': '房间', 'location_id': 'LOC0001',
                                 'variant': 'base', 'parent_id': None, 'visual_facts': []}]
        data['prop_coverage_review'].update(reviewed_full_script=True, reviewed_scene_ids=['SC0001'])
        ledger.validate(ledger.parse_script(output), paragraphs, data)
        bad = copy.deepcopy(data)
        bad['characters'][0]['source_speaker'] = '阿明'
        with self.assertRaisesRegex(ValueError, '换装'):
            ledger.validate(ledger.parse_script(output), paragraphs, bad)

    def test_name_mapping_cannot_change_dialogue_or_punctuation(self):
        self.fixture()
        original = self.draft.read_text(encoding='utf-8')
        for old, new in [('阿明，叫', '阿明-黑衣，叫'), ('来！', '来。'), ('「别改。」', '“别改。”')]:
            with self.subTest(new=new):
                save(self.draft, original.replace(old, new))
                self.assertNotEqual(review.make_report(self.project, {})['status'], 'passed')

    def test_action_and_speaker_must_use_full_name(self):
        self.fixture()
        original = self.draft.read_text(encoding='utf-8')
        for old, new in [('△ 阿明-黑衣', '△ 阿明'), ('\n阿明-黑衣：', '\n阿明：')]:
            save(self.draft, original.replace(old, new))
            self.assertNotEqual(review.make_report(self.project, {})['status'], 'passed')

    def test_fragment_mapping_keeps_original_source_coordinates(self):
        paragraphs = self.fixture()
        report = review.make_report(self.project, {})
        row = report['scenes'][0]['paragraph_map'][1]
        raw = paragraphs[1]
        target = self.draft.read_text(encoding='utf-8').splitlines()[2]
        mapping = dict(row['fragment_template'], reason='只映射本场显式换装名称，保留其余正文', fragments=[{
            'source_range': [0, len(raw)], 'source_text': raw,
            'target': {'line': 3, 'kind': 'action', 'range': [2, len(target)], 'text': target[2:]}}])
        result = review.make_report(self.project, {'fragments': {row['fragment_key']: mapping}})
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['scenes'][0]['paragraph_map'][1]['fragments'][0]['source_text'], raw)

    def test_dialogue_internal_spaces_and_name_suffixes_remain_exact(self):
        self.fixture(dialogue='阿明，  阿明-黑衣：「原字，别改！」')
        self.assertEqual(review.make_report(self.project, {})['status'], 'passed')
        save(self.draft, self.draft.read_text(encoding='utf-8').replace('，  阿明', '， 阿明'))
        self.assertNotEqual(review.make_report(self.project, {})['status'], 'passed')

    def test_new_policy_keeps_dialogue_exact_even_without_clothing(self):
        project = self.work / 'plain'
        save(project / 'source.json', {'character_name_policy': names.POLICY,
             'paragraphs': [{'id': 1, 'text': '阿明：“阿明，  别动！”'}]})
        path = project / 'scenes/scene_001_0001-0001.txt'
        prefix = '场景1-日-内-房间\n人物：阿明-基础形象；\n阿明：'
        save(path, prefix + '阿明，  别动！')
        self.assertEqual(review.make_report(project, {})['status'], 'passed')
        for text in ('阿明， 别动！', '阿明，  别动。'):
            save(path, prefix + text)
            self.assertNotEqual(review.make_report(project, {})['status'], 'passed')

    def test_longest_names_qualified_names_and_quotes_are_protected(self):
        mapping = {'阿明': '阿明-黑衣', '阿明月': '阿明月-白衣'}
        self.assertEqual(names.action_names('阿明看向阿明月，阿明-黑衣说“阿明，来。”', mapping),
                         '阿明-黑衣看向阿明月-白衣，阿明-黑衣说“阿明，来。”')

    def test_next_scene_without_explicit_clothing_does_not_inherit(self):
        paragraphs = self.fixture()
        paragraphs += ['人物：阿明；', '阿明停步。', '阿明：阿明，别动。']
        settings = json.loads((self.project / 'source.json').read_text(encoding='utf-8'))
        settings['paragraphs'] = [{'id': i, 'text': text} for i, text in enumerate(paragraphs, 1)]
        save(self.project / 'source.json', settings)
        path = self.project / 'scenes/scene_002_0004-0006.txt'
        save(path, '场景2-日-外-院落\n人物：阿明-基础形象；\n△ 阿明停步。\n阿明：阿明，别动。\n')
        result = review.check_scene(paragraphs, 4, 6, path, {})
        self.assertEqual(result['coverage_missing'], [])
        self.assertEqual(result['coverage_extra'], [])
        self.assertFalse(any(item['issue'] for item in result['speaker_items']))

    def test_legacy_project_keeps_original_speaker_contract(self):
        self.fixture(legacy=True)
        # Old projects do not acquire scene-name transformations on upgrade.
        self.assertNotEqual(review.make_report(self.project, {})['status'], 'passed')

    def test_explicit_speaker_set_is_exact_in_new_and_legacy_review(self):
        for new_policy in (False, True):
            for quoted in (False, True):
                for source_speaker in ('阿明', '阿明、阿明月'):
                    with self.subTest(new=new_policy, quoted=quoted, source=source_speaker):
                        project = self.work / f'voice-{new_policy}-{quoted}-{source_speaker}'
                        text = '阿明，别动。'
                        speech = '“' + text + '”' if quoted else text
                        paragraphs = (['人物：阿明-黑衣；阿明月-白衣；'] if new_policy else []) + [source_speaker + '：' + speech]
                        settings = {'paragraphs': [{'id': i, 'text': value} for i, value in enumerate(paragraphs, 1)]}
                        if new_policy:
                            settings['character_name_policy'] = names.POLICY
                        save(project / 'source.json', settings)
                        speaker = '阿明-黑衣、阿明月-白衣' if new_policy else '阿明、阿明月'
                        path = project / f'scenes/scene_001_0001-{len(paragraphs):04d}.txt'
                        save(path, '场景1-日-内-房间\n人物：阿明-黑衣；阿明月-白衣；\n' + speaker + '：' + text)
                        report = review.make_report(project, {})
                        if source_speaker == '阿明、阿明月':
                            self.assertEqual(report['status'], 'passed', report)
                        else:
                            self.assertEqual(report['status'], 'needs_review', report)
                            item = next(item for item in report['scenes'][0]['speaker_items'] if item['issue'])
                            decision = dict(item['decision_template'], speaker=speaker)
                            self.assertEqual(review.make_report(project, {item['decision_key']: decision})['status'], 'needs_review')

    def test_old_review_report_cannot_assemble_after_real_algorithm_change(self):
        self.fixture()
        report = review.make_report(self.project, {})
        report['review_version'] = review.REVIEW_VERSION - 1
        save(self.project / 'old-review.json', report)
        with self.assertRaisesRegex(ValueError, '当前版本'):
            script_io.assemble(self.project, self.project / 'script.txt', self.project / 'old-review.json')

    def test_v9_observation_snapshot_stays_readable_but_new_import_rechecks(self):
        import scene_observations
        import test_asset_visual_design as fixture_module
        _, source, paragraphs, _, _ = fixture_module.fixture(self.work / 'observations', with_prop=False)
        project = source.parent
        report = review.make_report(project, {})
        payload = {'source_digest': report['fingerprint']['source_digest'], 'scenes': [
            {'file': item['file'], 'scene_number': i, 'source_range': item['source_range'],
             'draft_sha256': item['draft_hash']} for i, item in enumerate(report['scenes'], 1)]}
        snapshot = scene_observations._review_snapshot(report, payload)
        snapshot['review_version'] = 9
        self.assertEqual(scene_observations._review_scenes(snapshot, payload, paragraphs), {1, 2})
        report['review_version'] = 9
        with self.assertRaisesRegex(ValueError, '版本过期'):
            scene_observations._review_scenes(report, payload, paragraphs)

    def test_same_asset_keeps_scene_local_speakers_in_storyboard_parse(self):
        import director
        import storyboard_text
        paragraphs = ['人物：阿明-黑衣；', '阿明站在门边。', '阿明：别动。',
                      '人物：阿明；', '阿明仍穿黑衣站在院内。', '阿明：别动。']
        project = self.work / 'mixed'
        save(project / 'source.json', {'character_name_policy': names.POLICY,
             'paragraphs': [{'id': i, 'text': value} for i, value in enumerate(paragraphs, 1)]})
        bodies = ['场景1-日-内-房间\n人物：阿明-黑衣；\n△ 阿明-黑衣站在门边。\n阿明-黑衣：别动。',
                  '场景2-日-外-院落\n人物：阿明-黑衣；\n△ 阿明仍穿黑衣站在院内。\n阿明：别动。']
        for i, body in enumerate(bodies, 1):
            save(project / f'scenes/scene_{i:03d}_{(i-1)*3+1:04d}-{i*3:04d}.txt', body)
        script_path = project / 'script.txt'
        save(script_path, '\n\n'.join(bodies))
        with contextlib.redirect_stdout(io.StringIO()):
            ledger.initialize(script_path, project, project / 'ledger.json')
        data = json.loads((project / 'ledger.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data['characters']), 1)
        self.assertEqual(data['characters'][0]['scene_source_speakers'], {'SC0001': '阿明-黑衣', 'SC0002': '阿明'})
        data['scene_assets'] = []
        for i, scene in enumerate(data['scenes'], 1):
            scene['scene_asset_id'] = f'SET{i:04d}'
            data['scene_assets'].append({'id': f'SET{i:04d}', 'name': '房间' if i == 1 else '院落'})
        records, length = director.build_records(script_path.read_text(encoding='utf-8'), data)
        state = {'ledger': data, 'records': records, 'length': length}
        drafts = []
        for i in (1, 2):
            selected = [record for record in records if record['scene'] == f'SC{i:04d}']
            action, speech = selected
            drafts.append(f'''## SC{i:04d} / u01 / {action['id']}-{speech['id']}
人物：@{{CHAR0001}}；
场景：@{{SET{i:04d}}}；时间：白天；天气：未明示；
人物站位：@{{CHAR0001}}站在门边。
·00秒-04秒[镜头1-叮嘱]：（@{{CHAR0001}}的中景；转场：硬切）镜头向右平移，焦点保留人物与门框；@{{CHAR0001}}站定。音效：无。
声音[{speech['id']},0.2-2.8]：@{{CHAR0001}}说：“别动。”
''')
        units = storyboard_text.parse('\n'.join(drafts), state)
        self.assertEqual(len(units), 2)
        for unit in units:
            unit.update(id=unit['draft_key'], style='3D国风动漫')
            self.assertIn('别动。', storyboard_text.render_unit(state, unit))

    def test_single_character_name_ambiguity_has_existing_fragment_path(self):
        project = self.work / 'short-name'
        raw = '白看向白色衣袖。'
        save(project / 'source.json', {'character_name_policy': names.POLICY,
             'paragraphs': [{'id': 1, 'text': '人物：白-黑衣；'}, {'id': 2, 'text': raw}]})
        path = project / 'scenes/scene_001_0001-0002.txt'
        target = '△ 白-黑衣看向白色衣袖。'
        save(path, '场景1-日-内-房间\n人物：白-黑衣；\n' + target)
        report = review.make_report(project, {})
        self.assertEqual(report['status'], 'needs_review')
        row = report['scenes'][0]['paragraph_map'][1]
        mapping = dict(row['fragment_template'], reason='首字白是人物名，白色是衣袖颜色，实际阅读确认', fragments=[
            {'source_range': [0, 1], 'source_text': '白',
             'target': {'line': 3, 'kind': 'action', 'range': [2, 6], 'text': '白-黑衣'}},
            {'source_range': [1, len(raw)], 'source_text': raw[1:],
             'target': {'line': 3, 'kind': 'action', 'range': [6, len(target)], 'text': target[6:]}}])
        self.assertEqual(review.make_report(project, {'fragments': {row['fragment_key']: mapping}})['status'], 'passed')

    def test_normal_design_enforces_shared_anchor_and_image_dependency(self):
        import test_asset_visual_design as fixture_module
        _, _, _, data, payload = fixture_module.fixture(self.work / 'design', with_prop=False)
        data['characters'][0].pop('appearance', None)
        variant = copy.deepcopy(data['characters'][0])
        variant.update(id='CHAR0002', name='陈默-白衣', source_entry='陈默-白衣',
                       scene_clothing={'base_name': '陈默'}, source_speaker='陈默-白衣')
        data['characters'].append(variant)
        data['scenes'][1]['character_ids'] = ['CHAR0002']
        record = copy.deepcopy(payload['designs'][0])
        record.update(asset_id='CHAR0002', styling='白色交领长衣，深灰腰带与布鞋',
                      image_reference={'base_asset_id': 'CHAR0001', 'changes': ['改穿白色交领长衣'],
                                       'preserve': ['基础人物年龄、身体、面容和发型']})
        payload['designs'].append(record)
        unrelated = copy.deepcopy(data['characters'][0])
        unrelated.update(id='CHAR0003', name='陈默-另一身体-基础形象',
                         appearance={'identity': '同名另人', 'incarnation': '另一世'})
        data['characters'].append(unrelated)
        other_anchor = dict(payload['identity_anchors'][0], id='同名另人-另一世', age='六十岁男性')
        payload['identity_anchors'].append(other_anchor)
        other_record = dict(payload['designs'][0], asset_id='CHAR0003', identity_anchor_id='同名另人-另一世')
        payload['designs'].append(other_record)
        result = design.apply_design(data, payload, '古装写实电影')
        self.assertEqual(design.image_reference_asset_ids(result['characters'][1]), ['CHAR0001'])
        self.assertIn('@' + result['characters'][0]['name'] + ' ', result['characters'][1]['image_prompt'])
        self.assertNotIn(payload['identity_anchors'][0]['face'], result['characters'][1]['image_prompt'])
        self.assertNotEqual(result['characters'][0]['visual_design']['identity_anchor_id'],
                            result['characters'][2]['visual_design']['identity_anchor_id'])
        bad = copy.deepcopy(payload)
        bad['designs'][-2].pop('image_reference')
        with self.assertRaises(ValueError):
            design.apply_design(data, bad, '古装写实电影')
        bad = copy.deepcopy(payload)
        other = dict(payload['identity_anchors'][0], id='错误身体', age='另一年龄')
        bad['identity_anchors'].append(other)
        bad['designs'][-2]['identity_anchor_id'] = '错误身体'
        with self.assertRaises(ValueError):
            design.apply_design(data, bad, '古装写实电影')


if __name__ == '__main__':
    unittest.main()
