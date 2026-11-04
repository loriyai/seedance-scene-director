"""Selected context dependencies; synthetic fixtures never approve production."""
import copy
import unittest
from unittest.mock import patch

import director as d
import storyboard_context as context
import storyboard_text_delivery as delivery
import test_commit_retry_recovery as fixtures


class SelectedContextTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.CommitRetryRecoveryTests('runTest')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.project=self.fx.fx.project
        self.state=d.load(self.project)
        self.record=self.state['records'][-1]
        self.span=[self.record['start'],self.record['end']]
        self.scope=context.context_scope(self.state,self.span)

    def digest(self,state=None,scope=None):
        return context.context_hash(state or self.state,self.project,scope=scope or self.scope)

    def seeded(self):
        state=copy.deepcopy(self.state)
        a,b=self.span
        state['units']=[{'id':'before-a','span':[a-4,a],'version':1,'source_text':'before body'},
                        {'id':'after','span':[b,b+4],'version':1,'source_text':'after body'}]
        return state

    def test_unrelated_scene_and_same_scene_non_neighbour_do_not_change_hash(self):
        state=self.seeded();baseline=self.digest(state)
        for scene in ('SC0001','SC0002'):
            other=copy.deepcopy(state)
            other['units'].append({'id':'unrelated-'+scene,'span':[0,1],'main_scene':scene,
                                   'version':7,'source_text':'irrelevant body'})
            self.assertEqual(self.digest(other),baseline)
            self.assertNotEqual(context.context_hash(other,self.project),context.context_hash(state,self.project))

    def test_neighbours_all_equal_boundaries_crossing_and_contained_units_are_bound(self):
        state=self.seeded();a,b=self.span
        state['units'] += [{'id':'before-b','span':[a-3,a],'version':2,'source_text':'other before'},
                           {'id':'crossing','span':[a-1,b+1],'version':1,'source_text':'crossing'},
                           {'id':'inside','span':[a,b],'version':1,'source_text':'inside'}]
        baseline=self.digest(state)
        for index in range(len(state['units'])):
            for field,value in (('version',99),('source_text','changed actual body')):
                other=copy.deepcopy(state);other['units'][index][field]=value
                self.assertNotEqual(self.digest(other),baseline,(index,field))
        other=copy.deepcopy(state);other['units'].pop(2)
        self.assertNotEqual(self.digest(other),baseline)

    def test_explicit_cross_scene_carry_anchor_and_actor_relation_are_bound(self):
        state=self.seeded()
        anchor={'id':'far-anchor','span':[0,1],'version':1,'source_text':'actual anchor'}
        actor={'id':'far-actor','span':[1,2],'version':1,'source_text':'actual actor',
               'production_cast':[{'actor_key':'stable-body'}]}
        state['units'] += [anchor,actor]
        scope=copy.deepcopy(self.scope)
        scope['references']['continuity_props']=[{'anchor':{'unit_id':'far-anchor','version':1}}]
        scope['references']['production_cast']=[{'actor_key':'stable-body'}]
        baseline=self.digest(state,scope)
        for index in (2,3):
            other=copy.deepcopy(state);other['units'][index]['source_text']='changed far dependency'
            self.assertNotEqual(self.digest(other,scope),baseline)
        self.assertEqual(self.digest(state),self.digest(self.seeded()))

    def test_global_source_ledger_style_header_and_rule_changes_still_invalidate(self):
        baseline=self.digest()
        for field in ('records','ledger','style_hash','header_hash','header_policy','entry'):
            other=copy.deepcopy(self.state)
            if field=='records':other[field][0]['text']+=' source change'
            elif field=='ledger':other[field]['characters'][0]['name']+=' changed'
            else:other[field]={'changed':True}
            with self.subTest(field=field):
                if field=='header_hash':
                    with self.assertRaises(ValueError):self.digest(other)
                else:self.assertNotEqual(self.digest(other),baseline)
        original=d.file_hash
        with patch.object(d,'file_hash',side_effect=lambda p:'changed-rule' if str(p).endswith('storyboard-rules.md') else original(p)):
            self.assertNotEqual(self.digest(),baseline)

    def test_local_and_related_history_are_scoped_and_neighbour_content_is_bound(self):
        state=self.seeded();a,b=self.span
        sibling={'units':[{'id':'foreign-before','span':[a-2,a],'version':1,'source_text':'foreign'}]}
        related=[{'session':'foreign/session.json','sha256':'whole-session-one','ranges':[[a-2,a]],
                  'receipts':[{'request_id':'foreign','span':[a-2,a],'output_sha256':'foreign-output'}]}]
        import project_continuation as continuation
        original=d.read_json
        with patch.object(continuation,'related',side_effect=lambda p:related), \
             patch.object(d,'read_json',side_effect=lambda p:sibling if str(p).endswith('foreign\\session.json') or str(p).endswith('foreign/session.json') else original(p)):
            baseline=self.digest(state)
            sibling['units'].append({'id':'unrelated-foreign','span':[0,1],'version':1,'source_text':'far'})
            related[0]['sha256']='whole-session-two'
            related[0]['ranges'].append([0,1])
            related[0]['receipts'].append({'request_id':'far','span':[0,1],'output_sha256':'far-output'})
            self.assertEqual(self.digest(state),baseline)
            sibling['units'][0]['version']=2
            self.assertNotEqual(self.digest(state),baseline)

    def test_local_delivery_uses_nearest_boundary_and_actual_content(self):
        import project_continuation as continuation
        a,b=self.span
        for name in ('near-draft','near-actual','near-review','far-draft','far-actual','far-review'):
            (self.project/name).write_text(name,encoding='utf-8')
        local=[{'segment':'local-near','span':[a-2,a],'draft':str(self.project/'near-draft'),
                'actual':str(self.project/'near-actual'),'review':str(self.project/'near-review')}]
        with patch.object(continuation,'deliveries',side_effect=lambda p:local):
            baseline=self.digest(self.seeded())
            local.append({'segment':'local-far','span':[0,1],'draft':str(self.project/'far-draft'),
                          'actual':str(self.project/'far-actual'),'review':str(self.project/'far-review')})
            self.assertEqual(self.digest(self.seeded()),baseline)
            (self.project/'near-actual').write_text('changed actual neighbour',encoding='utf-8')
            self.assertNotEqual(self.digest(self.seeded()),baseline)

    def test_real_unrelated_commit_preserves_bound_preview_and_review(self):
        # Existing actual boundary in SC2 separates this candidate from a later
        # commit in SC1; adding that distant historical gap must not stale it.
        near,near_span=self.fx.select('nearest-before',[2])
        self.fx.commit('nearest-before',near,near_span)
        draft,span=self.fx.select('selected-candidate',[3])
        state,units,before=delivery.prepare(self.project,draft,span)
        self.assertEqual(context.scene_context(self.project,'SC0002',span,
                         delivery.storyboard_authoring_gate.trace_path(draft))['versions']['context_sha256'],before['context_sha256'])
        first=delivery.preview(self.project,draft,span=span)
        other,other_span=self.fx.select('unrelated-first-scene',[0,1])
        _,_,info=delivery.prepare(self.project,other,other_span)
        # Synthetic user-owned range choice, not permission inferred in production.
        gate=info['authoring_gate']; review={k:info[k] for k in ('draft_sha256','context_sha256')}
        review.update(reviewer='synthetic-only',semantic_review='performed',
            authoring_trace_sha256=gate['trace_sha256'],authoring_gate_digest=gate['gate_digest'],
            shot_count=gate['shot_count'],reviewed_shots=gate['shot_keys'],
            scenes=[{'scene_id':'SC0001','status':'passed','issues':[],'basis':'Synthetic scope test only.'}])
        path=self.project/'unrelated-review.json';d.write_json(path,review)
        delivery.commit(self.project,other,'unrelated-commit',path,self.project/'unrelated-output.txt',other_span,
                        range_confirmation=info['history']['confirmation_token'])
        _,_,after=delivery.prepare(self.project,draft,span)
        self.assertEqual(before['context_sha256'],after['context_sha256'])
        self.assertEqual(before['authoring_gate']['gate_digest'],after['authoring_gate']['gate_digest'])
        self.assertTrue(delivery.preview(self.project,draft,span=span)['preview_reused'])
        # A true preceding body change rejects the already bound candidate.
        current=d.load(self.project)
        current['units'][0]['version']+=1
        d.write_json(self.project/'session.json',current)
        with self.assertRaises(ValueError):delivery.prepare(self.project,draft,span)

    def test_old_unmarked_trace_keeps_global_protocol(self):
        self.assertEqual(context.candidate_context_policy({'context_sha256':'old'}),context.LEGACY_CONTEXT_POLICY)
        self.assertIsNone(context.context_scope(self.state,self.span,policy=context.LEGACY_CONTEXT_POLICY))
        with self.assertRaises(ValueError):context.candidate_context_policy({'context_policy':'unknown'})

    def test_metadata_binding_does_not_upgrade_an_opaque_legacy_digest(self):
        import storyboard_authoring_gate as gate
        trace={'policy':'shot-by-shot-v1','version':4}
        units=[{'span':self.span}]
        legacy=gate.bind_metadata(trace,b'synthetic authored bytes',units,'opaque-global-digest')
        self.assertNotIn('context_policy',legacy)
        self.assertEqual(context.candidate_context_policy(legacy),context.LEGACY_CONTEXT_POLICY)
        scoped=gate.bind_metadata(trace,b'synthetic authored bytes',units,'opaque-selected-digest',
                                 context_policy=context.SCOPED_CONTEXT_POLICY)
        self.assertEqual(context.candidate_context_policy(scoped),context.SCOPED_CONTEXT_POLICY)


if __name__=='__main__':unittest.main()
