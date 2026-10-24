"""Fictional fixtures only; no production review facts or cloud writes."""
import copy
import json
import unittest
from unittest.mock import patch
import scene_review as sr
import review_gate as gate
import storyboard_preflight as pf
import authoring_gates as ag
import test_scene_review as legacy
import test_storyboard_preflight as audio_fixture
import test_canvas_reference_workflow as canvas_fixture
import test_audit_director as director_fixture
import canvas as c
import director as d


def fine_fixture():
    state, request=legacy.fixture()
    for i,u in enumerate(request['units']):
        u['shots'].append(copy.deepcopy(u['shots'][1]))
        for n,s in enumerate(u['shots']):
            s['review_id']=f'U{i+1}S{n+1}'
            s['composition']+=str(n)
            s['start'],s['end']=n,n+1
        u['review_dependencies']={'policy':'shot-dependencies-v1','complete':True,
            'basis':'虚构测试三镜各自观察车头、停止线和路缘；没有额外跨镜轴线依赖。','links':[]}
    return state,request


class FineReviewTests(unittest.TestCase):
    def setUp(self):
        self.rules=patch.object(sr,'rules_digest',return_value='fixture-current-rules')
        self.rules.start(); self.addCleanup(self.rules.stop)
        self.state,self.request=fine_fixture()
        self.old=sr.freeze(self.state,self.request,validation_passed=True)
        self.review=legacy.reviewed(self.state,self.request)
        self.seal=sr.digest(self.review)

    def run_change(self, request=None, old=None, review=None, seal=None):
        return sr.prepare_incremental(old or self.old, review or self.review,self.state,
            request or self.request,validation_passed=True,expected_report_sha256=seal or self.seal)

    def pending(self, proof):return [x['key'] for x in proof['shot_provenance'] if x['status']=='pending']

    def test_single_interior_camera_change_and_exact_provenance(self):
        new=copy.deepcopy(self.request);new['units'][2]['shots'][1]['camera']='固定机位降低到车轮高度'
        report,proof=self.run_change(new)
        self.assertEqual(self.pending(proof),['3:2'])
        self.assertEqual(report['status'],'pending')
        self.assertEqual(proof['shot_provenance'][6]['old_report_sha256'],self.seal)
        self.assertEqual(proof['shot_provenance'][6]['old_target_key'],'3:1')

    def test_axis_dependency_and_boundary_transitive_closure(self):
        self.request['units'][2]['review_dependencies']['links']=[{
            'from':'U3S2','to':'U3S3','kind':'axis','basis':'虚构第三镜沿第二镜所建立车头朝向保持拍摄轴线。'}]
        self.old=sr.freeze(self.state,self.request,validation_passed=True)
        self.review=legacy.reviewed(self.state,self.request);self.seal=sr.digest(self.review)
        new=copy.deepcopy(self.request);new['units'][2]['shots'][1]['camera']='反向越轴机位'
        _,proof=self.run_change(new)
        self.assertEqual(self.pending(proof),['3:2','3:3','4:1'])

    def test_unchanged_text_changed_entering_state_cannot_reuse(self):
        new=copy.deepcopy(self.request)
        new['units'][2]['continuity']['initial']['car']['facts']['position']='road'
        _,proof=self.run_change(new)
        self.assertIn('3:2',self.pending(proof))

    def test_audio_or_fragment_change_invalidates_whole_unit(self):
        for part in (False,True):
            new=copy.deepcopy(self.request)
            if part:new['units'][2]['shots'][1]['dialogue_parts']=[{'record':'L3','pause':.5}]
            else:new['units'][2]['audio'][0]['pause']=.5
            _,proof=self.run_change(new)
            self.assertTrue({'3:1','3:2','3:3'}<=set(self.pending(proof)))

    def test_state_restoration_shot_reviewed_later_exact_state_reused(self):
        self.request['units']=self.request['units'][2:3]
        self.old=sr.freeze(self.state,self.request,validation_passed=True)
        self.review=legacy.reviewed(self.state,self.request);self.seal=sr.digest(self.review)
        new=copy.deepcopy(self.request);u=new['units'][0]
        u['continuity']['initial']['car']['facts']['position']='road'
        u['shots'][1]['continuity']['changes']=[{'entity':'car','set':{'facts':{'position':'curb'}},
             'evidence':[{'record':'L3','quote':'小车停稳'}]}]
        _,proof=self.run_change(new)
        self.assertEqual(self.pending(proof),['1:1','1:2'])

    def test_insert_delete_reorder_never_assigns_positional_old_judgment(self):
        for mode in ('insert','delete','reorder'):
            new=copy.deepcopy(self.request);shots=new['units'][2]['shots']
            if mode=='insert':
                s=copy.deepcopy(shots[0]);s['review_id']='new-shot';shots.insert(1,s)
            elif mode=='delete':shots.pop(1)
            else:shots[0],shots[1]=shots[1],shots[0]
            _,proof=self.run_change(new)
            pending=set(self.pending(proof))
            self.assertTrue({f'3:{n+1}' for n in range(len(shots))} <= pending)
            self.assertIn('2:3',pending)  # Actual incoming boundary changed.
            self.assertNotIn('1:1',pending)
            self.assertNotIn('5:2',pending)
            self.assertGreater(proof['stats']['reused_shots'],0)

    def test_tampered_report_snapshot_and_missing_dependencies(self):
        bad=copy.deepcopy(self.review);bad['shot_reviews'][0]['basis']='被替换的虚构依据，不是原审阅记录。'
        with self.assertRaisesRegex(ValueError,'摘要'):self.run_change(review=bad)
        snap=copy.deepcopy(self.old);snap['request']['units'][0]['global']='改过'
        with self.assertRaisesRegex(ValueError,'快照'):self.run_change(old=snap)
        new=copy.deepcopy(self.request);new['units'][2]['review_dependencies']['links']=[{
            'from':'missing','to':'U3S2','kind':'occlusion','basis':'虚构遮挡来源不存在，不能证明独立性。'}]
        _,proof=self.run_change(new);self.assertIn('3:2',self.pending(proof))

    def test_unchanged_and_final_gate_recomputes_copied_evidence(self):
        report,proof=self.run_change();self.assertEqual(proof['stats']['reused_shots'],18)
        report['status']='passed';report['reviewer']='fictional final reviewer'
        report['checks']=copy.deepcopy(self.review['checks'])
        gate.verify(self.state,self.request,report,[])
        report['shot_reviews'][0]['basis']='篡改虚构旧判断后不能冒充原始审阅证据。'
        with self.assertRaisesRegex(ValueError,'复用判断'):gate.verify(self.state,self.request,report,[])

    def test_warning_reuse_needs_digest_and_final_exact_evidence(self):
        warning={'code':'FIXTURE_WARNING','unit':'U3','message':'虚构测试警告'}
        old=sr.freeze(self.state,self.request,[warning],validation_passed=True)
        review=legacy.reviewed(self.state,self.request,[warning])
        report,proof=sr.prepare_incremental(old,review,self.state,self.request,[warning],validation_passed=True)
        self.assertEqual(proof['stats']['reused_warnings'],0)
        self.assertEqual(proof['warning_provenance'][0]['status'],'pending')
        report,proof=sr.prepare_incremental(old,review,self.state,self.request,[warning],validation_passed=True,expected_report_sha256=sr.digest(review))
        self.assertEqual(proof['stats']['reused_warnings'],1)
        sr.verify_reuse_evidence(self.state,self.request,report,[warning])
        report['warning_decisions'][0]['basis']='被替换的虚构警告依据，与真实旧记录不同。'
        with self.assertRaisesRegex(ValueError,'复用警告'):
            sr.verify_reuse_evidence(self.state,self.request,report,[warning])

    def test_rule_version_and_missing_report_digest_fail_closed(self):
        with patch.object(sr,'rules_digest',return_value='changed-rules'):
            _,proof=self.run_change();self.assertEqual(proof['stats']['reused_shots'],0)
        _,proof=sr.prepare_incremental(self.old,self.review,self.state,self.request,validation_passed=True)
        self.assertEqual(proof['stats']['reused_shots'],0)


def speech_fixture(end=1.0):
    u=audio_fixture.unit('请你稍等片刻。',units=6,end=end)
    u.update(workflow_policy=ag.POLICY,duration=4,span=[0,6],action_plan=[])
    state=audio_fixture.state();state['records']=[{'id':'L1','scene':'SC1','kind':'dialogue',
        'text':'请你稍等片刻。','speaker':'阿明','voice':'说','start':0,'end':6}]
    return state,u


def approve_fictional_audio(state,u,basis='六个音节以急促催促语气连读，句内零停连句末短停，当前一秒窗口可执行。'):
    report=ag.audio_ready(state,u,pf.check_audio_plan(u))
    u['audio_preflight']=copy.deepcopy(report['semantic_template'])
    u['audio_preflight'].update(reviewer='fictional writer',basis=basis)
    for w in u['audio_preflight']['warning_decisions']:
        w.update(decision='accepted',basis=basis)


class AudioGateTests(unittest.TestCase):
    def test_unhandled_warning_blocks_and_specific_exception_can_proceed(self):
        state,u=speech_fixture();checked=pf.check_audio_plan(u)
        self.assertEqual(checked['status'],'passed');self.assertTrue(checked['warnings'])
        self.assertEqual(ag.audio_ready(state,u,checked)['status'],'blocked')
        approve_fictional_audio(state,u)
        self.assertEqual(ag.audio_ready(state,u,checked)['status'],'ready')

    def test_saved_entry_minimum_blocks_short_sound_plan(self):
        state,u=speech_fixture();state['entry']={'min':6,'max':30,'durations':None}
        self.assertIn('AUDIO_ENTRY_DURATION',{e['code'] for e in ag.audio_ready(state,u,pf.check_audio_plan(u))['errors']})

    def test_generic_repeated_basis_never_authorizes(self):
        state,u=speech_fixture();approve_fictional_audio(state,u,'已检查，符合剧情，为了节奏，符合要求。')
        self.assertEqual(ag.audio_ready(state,u,pf.check_audio_plan(u))['status'],'blocked')

    def test_sound_change_invalidates_camera_only_does_not(self):
        state,u=speech_fixture();approve_fictional_audio(state,u)
        u['shots']=[{'start':0,'end':4,'camera':'从人物左侧拍摄','dialogue_parts':[
            dict(p,record='L1') for p in u['audio_plan'][0]['parts']]}]
        self.assertEqual(ag.audio_ready(state,u,pf.check_audio_plan(u))['status'],'ready')
        u['audio'][0]['pause']=.3;u['audio_plan'][0]['parts'][0]['pause']=.3
        u['shots'][0]['dialogue_parts'][0]['pause']=.3
        self.assertEqual(ag.audio_ready(state,u,pf.check_audio_plan(u))['status'],'blocked')

    def test_divergence_and_impossible_integer_partition(self):
        state,u=speech_fixture();u['shots']=[{'start':0,'end':4,'dialogue_parts':[dict(u['audio_plan'][0]['parts'][0],record='L1',end=1.1)]}]
        self.assertIn('AUDIO_PLAN_DIVERGED',{e['code'] for e in pf.check_audio_plan(u)['errors']})
        state,u=speech_fixture();u['duration']=8
        text='甲乙丙丁戊己庚辛壬癸子丑寅卯，辰巳午未申酉戌亥天地玄黄宇宙。'
        u['audio'][0].update(text=text,units=28,end=6.6,pause=0)
        parts=[{'text':text[:15],'start':0,'end':3.3,'pause':0},{'text':text[15:],'start':3.3,'end':6.6,'pause':0}]
        u['audio_plan'][0]['parts']=parts
        state['records'][0].update(text=text,end=28);u['span']=[0,28]
        result=ag.audio_ready(state,u,pf.check_audio_plan(u))
        self.assertIn('AUDIO_CUT_PARTITION',{e['code'] for e in result['errors']})


class NoBlackTests(unittest.TestCase):
    sync=canvas_fixture.ReferenceWorkflowChecks.sync
    prepared=canvas_fixture.ReferenceWorkflowChecks.prepared
    def setUp(self):
        canvas_fixture.ReferenceWorkflowChecks.setUp(self)
        self.unit=self.session['units'][0]
        self.unit.update(black_reference_mode='none-v1',display_name='B01-场景1-分镜1',
                         test_prompt='B01-场景1-分镜1（时长：4秒）\n@Actor @Room @Pen action')
        self.session['units']=[self.unit]
    def test_no_lookup_creation_edge_or_missing_black_report(self):
        with (patch.object(c,'black_reference',side_effect=AssertionError('must not query black')),
              patch.object(c,'reconcile_black_video',side_effect=AssertionError('must not prepare black'))):
            result=self.sync()
        self.assertEqual(result['status'],'synced')
        self.assertNotIn('create_edge',self.cli.calls)
        self.assertFalse(any(x.get('key')=='BLACK' for x in result['reference_issues']))


class StagingAndNamingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=director_fixture.AuditDirectorChecks();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
    def test_staging_actual_render_and_state_binding(self):
        state=self.fixture.state;u=self.fixture.unit([0,9]);u['style']='3D国漫'
        pending=ag.staging_pending(state,u)
        self.assertEqual(pending['status'],'pending')
        u['staging_review']=dict(pending,status='reviewed',reviewer='fictional writer',
            render_basis='虚构四镜实际渲染中站位和观看焦点与窗边动作一致。',
            timing_state_basis='四个整数秒镜头连续，后台没有新增持物或位置变化。')
        ag.validate_staging(state,u)
        u['shots'][0]['composition']='虚构人物已离开画框右侧'
        with self.assertRaisesRegex(ValueError,'过期'):ag.validate_staging(state,u)
    def test_current_life_display_normalization_preserves_identity_and_collision_fails(self):
        import sys
        sys.path.insert(0,str(d.ROOT/'modules/screenplay-standardizer/scripts'))
        import asset_ledger as mod
        data={'schema_version':3,'locations':[],'scene_assets':[],'scenes':[],'props':[],
            'characters':[{'id':'CHAR0001','name':'卢筱筱-今生-基础形象','appearance':{'incarnation':'今生'}},
                          {'id':'CHAR0002','name':'卢筱筱-前世-基础形象'}]}
        result,report=mod.normalize_assets(data)
        self.assertEqual(result['characters'][0]['name'],'卢筱筱-基础形象')
        self.assertEqual(result['characters'][0]['source_entry'],'卢筱筱-今生-基础形象')
        self.assertEqual(result['characters'][0]['appearance']['incarnation'],'今生')
        self.assertEqual(result['characters'][1]['name'],'卢筱筱-前世-基础形象')
        data['characters'].append({'id':'CHAR0003','name':'卢筱筱-基础形象'})
        with self.assertRaisesRegex(ValueError,'重复'):mod.normalize_assets(data)

    def test_full_new_workflow_validation_requires_current_render_review(self):
        state=self.fixture.state;request=self.fixture.request()
        for u in request['units']:
            for shot in u['shots'][1::2]:
                shot.update(camera='相机向窗边横移',
                    purpose='移动取景呈现人物与窗边的位置关系。')
            u.update(style='3D国漫',workflow_policy=ag.POLICY,action_plan=[])
            # This fixture tests staging after the silent action plan is ready.
            approve_fictional_audio(state,u,'虚构样例中站起看窗按原文呈现，没有对白或额外需要登记的动作时间窗。')
        d.require_workflow(request)
        with self.assertRaisesRegex(ValueError,'冻结前'):d.validate(state,request)
        for u in request['units']:
            p=ag.staging_pending(state,u)
            u['staging_review']=dict(p,status='reviewed',reviewer='fictional writer',
                render_basis='虚构场景四镜的站姿和看窗动作在实际渲染中按顺序呈现。',
                timing_state_basis='虚构四秒单元按一秒镜头连续覆盖，状态重放与原登记相同。')
        d.validate(state,request)

    def test_requested_pose_cases_require_correlated_checks_and_replay(self):
        cases=[('女方离开，男方留在走廊递杯原处。','位置','女方面前','走廊递杯原处'),
               ('伞掉在地上，他不再撑伞。','持物','撑伞','双手空，伞已落地'),
               ('他跪下后保持跪姿。','姿态','站立','跪姿'),
               ('他把手机从耳侧移到身侧。','持物','手机在耳侧','手机在身侧')]
        for source,fact,before,after in cases:
            with self.subTest(source=source):
                state=copy.deepcopy(self.fixture.state);u=self.fixture.unit([0,9]);u['style']='3D国漫'
                r=state['records'][0];r.update(text=source,start=0,end=len(d.compact(source)))
                state['records']=[r];u['span']=[0,r['end']]
                u['continuity']['initial']['CHAR0001']['facts']={fact:before}
                u['continuity']['final']['CHAR0001']['facts']={fact:after}
                u['shots'][0]['continuity']['changes']=[{'entity':'CHAR0001','set':{'facts':{fact:after}},
                    'evidence':[{'record':r['id'],'quote':source}]}]
                u['shots'][0]['text']=source
                # Even if natural-language photography still says before,
                # program supplies a pending inspection, never semantic pass.
                u['shots'][1]['composition']=before
                p=ag.staging_pending(state,u)
                self.assertEqual(p['change_checks'][0]['status'],'pending')
                u['staging_review']=dict(p,status='reviewed',reviewer='fictional writer',
                    render_basis='虚构正文与摄影尚须核对真实动作后当前姿态和持物。',
                    timing_state_basis='声明变化已经重放到结束状态，须继续完成关联字段核对。')
                with self.assertRaisesRegex(ValueError,'关联字段'):ag.validate_staging(state,u)
                u['shots'][1]['composition']=after
                u['start_state']=before;u['end_state']=after
                p=ag.staging_pending(state,u)
                u['staging_review']=dict(p,status='reviewed',reviewer='fictional writer',
                    render_basis='虚构镜头正文和构图均更新到原事件结果，后续保持而不重复动作。',
                    timing_state_basis='原事件只登记一次，后台重放和结束状态均保持当前结果。')
                u['staging_review']['change_checks'][0].update(status='reviewed',basis=f'虚构样例核对{source}；本次关联字段均表达{after}，稳定室内光照无需重写。')
                ag.validate_staging(state,u)
                u['continuity']['final']['CHAR0001']['facts'][fact]=before
                with self.assertRaisesRegex(ValueError,'重放'):ag.staging_pending(state,u)

    def test_offscreen_and_valid_relative_location_are_not_exits(self):
        state=self.fixture.state;u=self.fixture.unit([0,9]);u['style']='3D国漫'
        for side in ('initial','final'):u['continuity'][side]['CHAR0001']['facts']={'位置':'窗前，面对未移动的同伴'}
        for s in u['shots']:
            s['subjects']=[];s['focus']=[]
            s['continuity']['visibility']['CHAR0001']={'mode':'offscreen','basis':'相机取窗框局部，人物在窗前未离场但在画幅外'}
        p=ag.staging_pending(state,u)
        self.assertEqual(p['change_checks'],[])
        self.assertTrue(u['continuity']['final']['CHAR0001']['present'])

    def test_measured_rework_records_affected_reviewed_shots(self):
        from review_metrics import measure
        state,old=fine_fixture();review=legacy.reviewed(state,old)
        snap=sr.freeze(state,old,validation_passed=True)
        new=copy.deepcopy(old);new['units'][2]['audio'][0]['pause']=.5
        _,proof=sr.prepare_incremental(snap,review,state,new,validation_passed=True,expected_report_sha256=sr.digest(review))
        result=measure(old,new,proof,'2026-10-16T10:00:00+00:00','2026-10-16T10:02:15+00:00')
        self.assertEqual(result['replan_count'],1)
        self.assertEqual(result['invalidated_previously_reviewed_shots'],3)
        self.assertEqual(result['measured_elapsed_seconds'],135)


if __name__=='__main__': unittest.main()
