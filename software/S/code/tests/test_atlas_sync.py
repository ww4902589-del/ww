"""Exercise persistence through real HTTP, isolated from the user's atlas."""
import http.client,json,pathlib,sys,tempfile,threading,unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'src')]
import atlas_server as S
import atlas_host
from atlas_resources import ResourceStore

class SyncTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=ResourceStore(self.tmp.name)
  self.base={'sets':[{'id':'a','name':'A','paths':['old#batch#1'],'note':{},'color':0}],'active':'a'}
  self.store.save('_marks.json',self.base)
  handler=type('IsolatedHandler',(S.Handler,),{'out_dir':self.tmp.name,'log':staticmethod(lambda *a:None)})
  self.srv=ThreadingHTTPServer(('127.0.0.1',0),handler)
  self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True);self.thread.start()
 def tearDown(self):
  self.srv.shutdown();self.srv.server_close();self.thread.join();self.tmp.cleanup()
 def req(self,method,path,body=None):
  c=http.client.HTTPConnection('127.0.0.1',self.srv.server_port)
  c.request(method,path,json.dumps(body) if body is not None else None,{'Content-Type':'application/json'})
  r=c.getresponse();result=(r.status,json.loads(r.read()));c.close();return result
 def test_two_pages_merge_their_changes_and_old_whole_write_is_blocked(self):
  base=self.req('GET','/api/marks')[1]
  a=json.loads(json.dumps(base));a['sets'][0]['paths'].append('added-by-a')
  self.assertEqual(self.req('POST','/api/marks/sync',{'baseline':base,'desired':a})[0],200)
  b=json.loads(json.dumps(base));b['sets'][0]['paths']=[]
  st,merged=self.req('POST','/api/marks/sync',{'baseline':base,'desired':b})
  self.assertEqual(st,200);self.assertEqual(merged['sets'][0]['paths'],['added-by-a'])
  self.assertEqual(self.req('POST','/api/marks',base)[0],409)
  self.assertEqual(self.req('GET','/api/marks')[1]['sets'][0]['paths'],['added-by-a'])
 def test_missing_grades_are_returned_and_not_silently_erased(self):
  self.store.save('_grades.json',{'grades':{'missing#batch#1':{'g':1}}})
  st,m=self.req('GET','/api/grades');self.assertEqual(st,200)
  self.assertIn('missing#batch#1',m['grades']);self.assertEqual(len(m['dead']),1)
 def test_same_note_conflict_is_reported_without_overwrite(self):
  base=self.req('GET','/api/marks')[1]
  desired=json.loads(json.dumps(base));desired['sets'][0]['note']={'old#batch#1':'A'}
  self.req('POST','/api/marks/sync',{'baseline':base,'desired':desired})
  desired['sets'][0]['note']={'old#batch#1':'B'}
  self.assertEqual(self.req('POST','/api/marks/sync',{'baseline':base,'desired':desired})[0],409)
  self.assertEqual(self.store.load('_marks.json')['sets'][0]['note']['old#batch#1'],'A')

class PackagingSeedTests(unittest.TestCase):
 def test_new_install_is_empty_and_upgrade_keeps_existing_collections(self):
  with tempfile.TemporaryDirectory() as directory:
   d=pathlib.Path(directory);bundle=d/'bundle';data=d/'data';bundle.mkdir();data.mkdir()
   initial=json.loads((ROOT/'tools/atlas_initial_data.json').read_text(encoding='utf8'))
   (bundle/'atlas_initial_data.json').write_text(json.dumps(initial),encoding='utf8')
   (bundle/'_grades_system.json').write_text('{"grades":{}}',encoding='utf8')
   with patch.object(sys,'_MEIPASS',str(bundle),create=True):
    atlas_host._seed_user_data(str(data))
    self.assertEqual(json.loads((data/'_marks.json').read_text(encoding='utf8'))['sets'][0]['paths'],[])
    current={'sets':[{'id':'kept','paths':['personal']}]}
    (data/'_marks.json').write_text(json.dumps(current))
    atlas_host._seed_user_data(str(data))
    self.assertEqual(json.loads((data/'_marks.json').read_text()),current)

if __name__=='__main__':unittest.main()
