"""Storyboard source identities survive user labels and image selections."""
import copy
import unittest

import canvas as c
import canvas_asset_reference_policy as policy


def document():
    return {'rootCanvasId': 'ROOT', 'assets': {
        'ROOT': {'type': 'canvas', 'content': {'nodes': {
            'ROLE': {'type': c.KINDS['role'], 'data': {'name': 'Actor'}},
            'VIDEO': {'type': c.KINDS['video'], 'data': {}}}, 'edges': {
                'role-edge': {'id': 'role-edge', 'source': 'ROLE', 'target': 'VIDEO', 'type': 'reference'}}}},
        'ROLE': {'pippitAssetId': 'ROLE', 'type': c.KINDS['role'], 'content': {
            'basicInfo': {'name': 'Actor'}, 'generation': {'prompt': 'original'},
            'appearanceInfo': {'pippitAssetId': 'NEW', 'assetId': 'NEW-MEDIA'},
            'sourceInfos': {'OLD': {'assetId': 'OLD-MEDIA', 'type': 'image'},
                            'NEW': {'assetId': 'NEW-MEDIA', 'type': 'image', 'isMain': True}}}},
        'VIDEO': {'pippitAssetId': 'VIDEO', 'type': c.KINDS['video'], 'content': {'generation': {
            'prompt': '<node-asset>ROLE</node-asset>', 'references': [{
                'sourceNodeId': 'ROLE', 'type': 'image', 'pippitAssetId': 'OLD', 'assetId': 'OLD-MEDIA'}]}}}}}


class BoundSourceChecks(unittest.TestCase):
    def test_name_prompt_and_selected_image_changes_preserve_source_identity(self):
        before = document()
        target = policy.source_target(None, before, 'ROLE', identity_only=True)
        after = copy.deepcopy(before)
        c.nodes(after)['ROLE']['data']['name'] = 'User label'
        c.content(after, 'ROLE')['basicInfo']['name'] = 'User label'
        c.content(after, 'ROLE')['generation']['prompt'] = 'User prompt'
        c.content(after, 'ROLE')['appearanceInfo'] = {'pippitAssetId': 'UPLOADED', 'assetId': 'MEDIA'}
        self.assertEqual(c.identity_fingerprint(before, 'ROLE'), c.identity_fingerprint(after, 'ROLE'))
        self.assertEqual(c.target_state(after, target), 'matched')
        after['assets']['ROLE']['pippitAssetId'] = 'OTHER'
        self.assertEqual(c.target_state(after, target), 'conflict')
        after = copy.deepcopy(before)
        del after['assets']['ROLE']['type']
        self.assertEqual(c.target_state(after, target), 'unknown')
        after = copy.deepcopy(before)
        c.nodes(after)['ROLE']['type'] = c.KINDS['scene']
        self.assertEqual(c.target_state(after, target), 'conflict')
        del after['assets']['ROLE']
        self.assertEqual(c.target_state(after, target), 'unknown')

    def test_retained_user_media_selection_is_valid_and_removed_media_is_refreshed(self):
        doc = document()
        item = {'prompt': '<node-asset>ROLE</node-asset>'}
        self.assertTrue(c.video_references_satisfied(doc, 'VIDEO', item))
        self.assertEqual(c.stale_role_reference_edges(doc, 'VIDEO', item['prompt']), [])
        del c.content(doc, 'ROLE')['sourceInfos']['OLD']
        self.assertFalse(c.video_references_satisfied(doc, 'VIDEO', item))
        self.assertEqual(c.stale_role_reference_edges(doc, 'VIDEO', item['prompt']), ['role-edge'])

    def test_scene_and_image_follow_official_edge_reference_contract(self):
        for kind in ('scene', 'image'):
            with self.subTest(kind=kind):
                doc = document()
                c.nodes(doc)['ROLE']['type'] = c.KINDS[kind]
                doc['assets']['ROLE']['type'] = c.KINDS[kind]
                if kind == 'image':
                    c.content(doc, 'ROLE').update(pippitAssetId='NEW', assetId='NEW-MEDIA')
                generation = c.content(doc, 'VIDEO')['generation']
                generation['references'] = []
                item = {'prompt': '<node-asset>ROLE</node-asset>'}
                self.assertTrue(c.video_references_satisfied(doc, 'VIDEO', item))
                generation['references'] = [{'sourceNodeId': 'ROLE', 'type': 'image', 'pippitAssetId': 'WRONG'}]
                self.assertFalse(c.video_references_satisfied(doc, 'VIDEO', item))


if __name__ == '__main__':
    unittest.main()
