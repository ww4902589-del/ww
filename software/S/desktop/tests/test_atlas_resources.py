import json, pathlib, sys, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from atlas_resources import ResourceStore, Conflict, merge_marks, merge_grades

class ResourceStoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.store=ResourceStore(self.root/'data')
 def tearDown(self):self.tmp.cleanup()
 def image(self,name,blob=b'image-one'):
  p=self.root/name;p.write_bytes(blob);return {'path':p.as_posix(),'uid':p.as_posix()+'#run#1','name':name}
 def test_id_and_old_reference_survive_external_move_and_restart(self):
  im=self.image('first.png');self.store.index([im]);rid=im['resource_id'];old=im['uid']
  p=self.root/'renamed.png';(self.root/'first.png').rename(p);im2={'path':p.as_posix(),'uid':p.as_posix()+'#other#2'}
  ResourceStore(self.root/'data').index([im2]);self.assertEqual(im2['resource_id'],rid)
  self.assertEqual(self.store.resolve(old),im2['uid'])
 def test_same_name_different_content_does_not_reuse_identity(self):
  im=self.image('first.png');self.store.index([im]);rid=im['resource_id'];(self.root/'first.png').unlink()
  im2=self.image('moved.png',b'different');self.store.index([im2]);self.assertNotEqual(im2['resource_id'],rid)
 def test_content_fingerprints_preserve_copy_identities_and_saved_state(self):
  images=[self.image('original.png'),self.image('copy.png'),self.image('other.png',b'different')]
  self.store.index(images)
  self.assertEqual(images[0]['content_hash'],images[1]['content_hash'])
  self.assertNotEqual(images[0]['content_hash'],images[2]['content_hash'])
  self.assertNotEqual(images[0]['resource_id'],images[1]['resource_id'])
  self.store.save('_marks.json',{'sets':[{'id':'a','paths':[images[1]['uid']]}]})
  self.store.save('_grades.json',{'grades':{images[1]['uid']:{'g':1}}})
  cached=[{k:v for k,v in im.items() if k!='content_hash'} for im in images]
  self.store.attach_content_hashes(cached)
  self.assertEqual([im['content_hash'] for im in cached],[im['content_hash'] for im in images])
  self.assertEqual(self.store.load('_marks.json')['sets'][0]['paths'],[images[1]['uid']])
  self.assertEqual(self.store.load('_grades.json')['grades'],{images[1]['uid']:{'g':1}})
 def test_stale_full_write_is_rejected_without_damage(self):
  base=self.store.load('_marks.json',{'sets':[]});self.store.save('_marks.json',{'sets':[{'id':'a','paths':['x']}]},expected=base['revision'])
  with self.assertRaises(Conflict):self.store.save('_marks.json',{'sets':[]},expected=base['revision'])
  self.assertEqual(self.store.load('_marks.json')['sets'][0]['paths'],['x'])
 def test_restart_keeps_document_and_json_export(self):
  self.store.save('_grades.json',{'grades':{'x':{'g':1}}});other=ResourceStore(self.root/'data')
  self.assertEqual(other.load('_grades.json')['grades']['x']['g'],1)
  self.assertEqual(json.loads((self.root/'data/_grades.json').read_text())['grades']['x']['g'],1)
 def test_legacy_file_is_imported_once_and_cannot_overwrite_authority(self):
  p=self.root/'data/_marks.json';p.write_text(json.dumps({'sets':[{'id':'a','paths':['kept']}]}))
  self.store.load('_marks.json');p.write_text(json.dumps({'sets':[]}))
  self.assertEqual(self.store.load('_marks.json')['sets'][0]['paths'],['kept'])
 def test_concurrent_changes_preserve_other_page_add_and_own_remove(self):
  base={'sets':[{'id':'a','name':'A','paths':['old'],'note':{}}],'active':'a'}
  current={'sets':[{'id':'a','name':'A','paths':['old','other'],'note':{}}],'active':'a'}
  desired={'sets':[{'id':'a','name':'A','paths':['new'],'note':{}}],'active':'a'}
  self.assertEqual(set(merge_marks(base,desired,current)['sets'][0]['paths']),{'new','other'})
 def test_deleted_set_is_not_resurrected_by_unchanged_stale_page(self):
  base={'sets':[{'id':'a','name':'A','paths':['x'],'note':{}},{'id':'b','paths':[]}]}
  current={'sets':[{'id':'b','paths':[]}]}
  self.assertEqual([s['id'] for s in merge_marks(base,base,current)['sets']],['b'])
 def test_grade_delta_does_not_drop_other_page_rating(self):
  self.assertEqual(merge_grades({'grades':{'a':{'g':1}}},{'grades':{}},{'grades':{'a':{'g':1},'b':{'g':0}}})['grades'],{'b':{'g':0}})

if __name__=='__main__':unittest.main()
