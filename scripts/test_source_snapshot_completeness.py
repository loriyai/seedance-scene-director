"""Read-only regression for a source first used after a partial batch readback."""
import copy
import unittest
import canvas as c
import canvas_batch as b

ROOT, SOURCE = 'ROOT', 'SOURCE-SECOND'

def fixture():
    doc={'rootCanvasId':ROOT,'assets':{ROOT:{'type':'canvas','content':{'nodes':{},'edges':{}}}}}
    for nid,kind in [(SOURCE,'role'),('SOURCE-FIRST','role')]+[(f'VIDEO{i}','video') for i in range(20)]:
        node={'id':nid,'type':'biz/'+kind,'data':{'name':nid}}
        doc['assets'][ROOT]['content']['nodes'][nid]=node
        doc['assets'][nid]={'type':'biz/'+kind,'pippitAssetId':nid,'content':{'basicInfo':{'name':nid},'generation':{'prompt':'original','references':[]},'appearanceInfo':{'pippitAssetId':'existing-media'}}}
    return doc

class Client:
    def __init__(self,doc):self.doc=doc;self.calls=[]
    def batch_snapshot(self,binding,node_ids=None):
        self.calls.append(node_ids)
        doc=copy.deepcopy(self.doc)
        if node_ids is not None:doc['assets']={key:value for key,value in doc['assets'].items() if key==ROOT or key in node_ids}
        return {'complete':True,'document':doc,'asset_versions':{key:2 for key in doc['assets']}}
    def command(self,*args):raise AssertionError('No write permitted')

class SourceCompletenessTests(unittest.TestCase):
    def setUp(self):self.doc=fixture();self.cli=Client(self.doc);self.state={'binding':{'canvas_id':ROOT}}
    def previous(self):
        targets=[{'action':'prompt_text','node_id':f'VIDEO{i}','value':'original'} for i in range(20)]
        targets.append({'action':'prompt_text','node_id':'SOURCE-FIRST','value':'original'})
        return b.readback(None,self.cli,self.state,None,targets)
    def snapshot(self,previous=None):return b.snapshot(self.cli,self.state,self.previous() if previous is None else previous,[{'node_id':'VIDEO1'}],sources=[SOURCE],source_identity_only=True)
    def test_source_first_used_in_second_batch_after_twenty_readbacks(self):
        previous=self.previous()
        self.assertIn(SOURCE,c.nodes(previous));self.assertNotIn(SOURCE,previous['assets'])
        before=copy.deepcopy(previous);fresh=self.snapshot(previous)
        self.assertEqual(fresh['document']['assets'][SOURCE],self.doc['assets'][SOURCE]);self.assertEqual(previous,before)
    def test_missing_fresh_asset_is_pending_not_gone(self):
        previous=self.previous();self.doc['assets'].pop(SOURCE)
        with self.assertRaises(c.CanvasError) as caught:self.snapshot(previous)
        self.assertEqual(caught.exception.code,'READBACK_PENDING')
    def test_complete_root_membership_removal_is_concurrent_edit(self):
        previous=self.previous();c.nodes(self.doc).pop(SOURCE)
        with self.assertRaises(c.CanvasError) as caught:self.snapshot(previous)
        self.assertEqual(caught.exception.code,'CONCURRENT_EDIT')
    def test_incomplete_root_membership_removal_is_pending(self):
        previous=self.previous();c.nodes(self.doc).pop(SOURCE)
        original=self.cli.batch_snapshot
        def incomplete(*args,**kwargs):result=original(*args,**kwargs);result['complete']=False;return result
        self.cli.batch_snapshot=incomplete
        with self.assertRaises(c.CanvasError) as caught:self.snapshot(previous)
        self.assertEqual(caught.exception.code,'READBACK_PENDING')
    def test_real_node_type_change_is_concurrent_edit(self):
        previous=self.previous();c.nodes(self.doc)[SOURCE]['type']='biz/image'
        with self.assertRaises(c.CanvasError) as caught:self.snapshot(previous)
        self.assertEqual(caught.exception.code,'CONCURRENT_EDIT')
    def test_real_asset_type_or_identity_change_is_concurrent_edit(self):
        for key,value in [('type','biz/image'),('pippitAssetId','DIFFERENT-ID')]:
            self.doc=fixture();self.cli.doc=self.doc;previous=self.previous();self.doc['assets'][SOURCE][key]=value
            with self.subTest(key=key),self.assertRaises(c.CanvasError) as caught:self.snapshot(previous)
            self.assertEqual(caught.exception.code,'CONCURRENT_EDIT')
    def test_missing_asset_type_or_content_is_pending(self):
        for key in ['type','content','pippitAssetId']:
            self.doc=fixture();self.cli.doc=self.doc;previous=self.previous();self.doc['assets'][SOURCE].pop(key)
            with self.subTest(key=key),self.assertRaises(c.CanvasError) as caught:self.snapshot(previous)
            self.assertEqual(caught.exception.code,'READBACK_PENDING')
    def test_incomplete_fresh_cannot_establish_source_identity(self):
        previous=self.previous();original=self.cli.batch_snapshot
        def incomplete(*args,**kwargs):result=original(*args,**kwargs);result['complete']=False;return result
        self.cli.batch_snapshot=incomplete
        with self.assertRaises(c.CanvasError) as caught:self.snapshot(previous)
        self.assertEqual(caught.exception.code,'READBACK_PENDING')
    def test_prior_partially_loaded_envelope_can_be_completed(self):
        previous=self.previous();previous['assets'][SOURCE]={'content':{}}
        self.assertIn(SOURCE,self.snapshot(previous)['document']['assets'])
    def test_prior_identity_envelope_missing_id_can_be_completed(self):
        previous=copy.deepcopy(self.doc);previous['assets'][SOURCE].pop('pippitAssetId')
        self.assertIn(SOURCE,self.snapshot(previous)['document']['assets'])
    def test_missing_previous_or_current_node_type_is_pending(self):
        for which in ['previous','current']:
            self.doc=fixture();self.cli.doc=self.doc;previous=self.previous()
            c.nodes(previous if which=='previous' else self.doc)[SOURCE].pop('type')
            with self.subTest(which=which),self.assertRaises(c.CanvasError) as caught:self.snapshot(previous)
            self.assertEqual(caught.exception.code,'READBACK_PENDING')
    def test_id_policy_accepts_source_prompt_name_media_and_layout_edits(self):
        previous=copy.deepcopy(self.doc)
        c.nodes(self.doc)[SOURCE]['data']['name']='user-renamed';c.nodes(self.doc)[SOURCE]['x']=999
        c.content(self.doc,SOURCE)['generation']['prompt']='user-prompt'
        c.content(self.doc,SOURCE)['appearanceInfo']['pippitAssetId']='user-media'
        self.assertIn(SOURCE,self.snapshot(previous)['document']['assets'])
    def test_target_full_business_protection_unchanged(self):
        previous=self.previous();c.content(self.doc,'VIDEO1')['generation']['prompt']='user-edit'
        with self.assertRaises(c.CanvasError) as caught:self.snapshot(previous)
        self.assertEqual(caught.exception.code,'CONCURRENT_EDIT')
    def test_full_source_business_policy_unchanged(self):
        previous=copy.deepcopy(self.doc);c.content(self.doc,SOURCE)['generation']['prompt']='user-edit'
        with self.assertRaises(c.CanvasError) as caught:b.snapshot(self.cli,self.state,previous,[{'node_id':'VIDEO1'}],sources=[SOURCE],source_identity_only=False)
        self.assertEqual(caught.exception.code,'CONCURRENT_EDIT')

if __name__=='__main__':unittest.main(verbosity=2)
