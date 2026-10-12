import copy
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'src')]
import atlas_management as M
from atlas_resources import ResourceStore


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.store = ResourceStore(self.root/'data')
        self.images = []
        for name, parent, key, value, date in [
            ('a', 'first', 'painting', ' 水彩 ', '2026-10-12T03:00:00+08:00'),
            ('b', 'first', 'painting', '水彩', '2025-10-12T03:00:00+08:00'),
            ('c', 'first', 'brush', '水彩', ''),
            ('d', 'second', 'painting', '水彩', '')]:
            folder = self.root/parent;folder.mkdir(exist_ok=True)
            path = folder/(name+'.png');path.write_bytes(name.encode())
            self.images.append({'path': path.as_posix(), 'uid': path.as_posix()+'#run#1',
                'folder': parent, 'produced_at': date, 'style_fields': [{'key': key, 'value': value}],
                'notes': '水彩', 'styles': []})
        self.store.index(self.images)
        self.data = {'images': self.images, 'facets': {}}
        M.decorate(self.data, self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_scope_key_trim_and_day_are_exact(self):
        folder = self.images[0]['folder_id']
        result = M.related(self.data, folder, 'painting', '水彩', page_size=1)
        self.assertEqual(result['total'], 2)
        self.assertEqual(len(result['matched_ids']), 2)
        self.assertEqual(self.images[0]['style_fields'][0]['value'], ' 水彩 ')
        self.assertEqual(M.related(self.data, folder, 'painting', '水彩', day='2026-10-12')['total'], 1)
        self.assertEqual(M.related(self.data, folder, 'brush', '水彩')['total'], 1)
        self.assertEqual(M.related(self.data, folder, 'painting', '油画')['total'], 0)

    def test_dates_never_use_mtime_or_review_time(self):
        self.assertEqual(self.images[2]['day'], '')
        self.assertEqual(self.images[0]['day'], '2026-10-12')
        self.assertEqual(self.images[1]['day'], '2025-10-12')
        self.assertEqual(M.produced_at({'updated_at':'2026-10-12T12:00:00'}), '')
        self.assertEqual(M.produced_at({'generated_at':'invalid'}), '')

    def test_create_assign_restart_and_move_keep_metadata_and_marks(self):
        uid = self.images[0]['image_uid']
        folder = M.organize(self.data, self.store, {'action':'create','name':'我的作品'})
        M.decorate(self.data, self.store)
        self.assertIn(folder, {f['id'] for f in self.data['management_folders']})
        M.organize(self.data, self.store, {'action':'assign','folder_id':folder,'image_uids':[uid]})
        original = self.images[0]
        self.store.save('_marks.json', {'sets':[{'id':'kept','paths':[original['uid']]}]})
        new_path = self.root/'renamed.png';pathlib.Path(original['path']).rename(new_path)
        moved = {'path':new_path.as_posix(),'uid':new_path.as_posix()+'#new#3','styles':[]}
        restarted = ResourceStore(self.root/'data');restarted.index([moved])
        data = {'images':[moved], 'facets':{}};M.decorate(data, restarted)
        self.assertEqual(moved['image_uid'], uid)
        self.assertEqual(moved['folder_id'], folder)
        self.assertEqual(moved['day'], '2026-10-12')
        self.assertEqual(moved['style_fields'][0]['norm'], '水彩')
        marks = restarted.canonical_marks(restarted.load('_marks.json'))
        self.assertEqual(marks['sets'][0]['paths'], [moved['uid']])

    def test_unknown_style_and_invalid_scope_do_not_match(self):
        self.assertEqual(M.fields({'note':'水彩'}), [])
        with self.assertRaises(ValueError):M.related(self.data, '', 'painting', '水彩')
        with self.assertRaises(ValueError):M.organize(self.data,self.store,{'action':'assign','folder_id':self.images[0]['folder_id'],'image_uids':['missing']})

    def test_real_generation_catalog_and_single_style_keys(self):
        painting=M.fields({'generation':{'styles':[{'catalog':'painting','name':' 水彩 '}]}})
        brush=M.fields({'generation':{'style_library':'brush','style_name':'水彩'}})
        self.assertEqual(painting[0]['key'],'catalog:painting')
        self.assertEqual(brush[0]['key'],'catalog:brush')
        self.assertNotEqual(painting[0]['key'],brush[0]['key'])
        self.assertEqual(painting[0]['value'],' 水彩 ')

    def test_disk_organization_target_survives_loss_of_its_source_images(self):
        target=self.images[0]['folder_id'];uid=self.images[3]['image_uid']
        M.organize(self.data,self.store,{'action':'assign','folder_id':target,'image_uids':[uid]})
        self.data['images']=[self.images[3]]
        M.decorate(self.data,self.store)
        self.assertEqual(self.images[3]['folder_id'],target)

    def test_reconstruction_does_not_roll_back_unobserved_concurrent_metadata(self):
        rid=self.images[3]['image_uid'];before=self.store.load('_organization.json')
        actual=self.store.load;calls=0
        def overlapping_load(*args,**kwargs):
            nonlocal calls
            value=actual(*args,**kwargs);calls+=1
            if calls==1:
                changed=copy.deepcopy(value)
                changed['metadata'][rid]['produced_at']='2026-10-11T12:00:00'
                self.store.save('_organization.json',changed,expected=value['revision'])
            return value
        data={'images':[dict(self.images[0],produced_at='2026-10-12T13:00:00')],'facets':{}}
        with patch.object(self.store,'load',side_effect=overlapping_load):M.decorate(data,self.store)
        self.assertEqual(actual('_organization.json')['metadata'][rid]['produced_at'],'2026-10-11T12:00:00')

    def test_ten_thousand_results_paging_with_all_ids(self):
        data = copy.deepcopy(self.data)
        base = data['images'][0]
        data['images'] = [dict(base,image_uid=str(i),uid=str(i),content_hash=str(i)) for i in range(10000)]
        start=time.perf_counter()
        result=M.related(data,base['folder_id'],'painting','水彩',page=300,page_size=24)
        self.assertEqual(result['total'],10000)
        self.assertEqual(len(result['images']),24)
        self.assertLess(time.perf_counter()-start,1.0)


if __name__ == '__main__':unittest.main()
