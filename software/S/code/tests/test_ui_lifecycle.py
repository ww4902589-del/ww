"""Closing the last UI must release both servers, without treating refresh as exit."""
import pathlib,sys,unittest,http.client,threading,json
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'src'))
from comfybatch_lifecycle import PageSessions
import comfybatch_lifecycle as UI
from http.server import ThreadingHTTPServer
from comfybatch_v2_app import Handler

class UiLifecycleTests(unittest.TestCase):
 def setUp(self):
  self.now=0.;self.pages=PageSessions(clock=lambda:self.now,close_grace=3,startup_grace=30)
 def test_last_page_close_stops_after_refresh_grace(self):
  token=self.pages.open('workbench');self.pages.end(token)
  self.now=2.9;self.assertFalse(self.pages.should_stop())
  self.now=3.1;self.assertTrue(self.pages.should_stop())
 def test_atlas_or_compare_keeps_program_alive(self):
  a=self.pages.open('workbench');b=self.pages.open('atlas');c=self.pages.open('compare')
  self.pages.end(a);self.pages.end(b);self.now=60;self.assertFalse(self.pages.should_stop())
  self.pages.end(c);self.now=64;self.assertTrue(self.pages.should_stop())
 def test_refresh_reconnect_and_hidden_tabs_do_not_exit(self):
  a=self.pages.open('first');self.pages.end(a);self.now=2
  b=self.pages.open('refreshed');self.now=600;self.assertFalse(self.pages.should_stop())
  self.pages.end(b);self.now=604;self.assertTrue(self.pages.should_stop())
 def test_close_beacon_releases_only_its_own_page(self):
  a=self.pages.open('a');b=self.pages.open('b');self.pages.close('a')
  self.now=60;self.assertFalse(self.pages.should_stop());self.pages.end(a)
  self.pages.close('b');self.now=64;self.assertTrue(self.pages.should_stop());self.pages.end(b)
 def test_no_browser_automation_waits_until_a_page_was_opened(self):
  self.pages=PageSessions(clock=lambda:self.now,close_grace=3,startup_grace=None)
  self.now=600;self.assertFalse(self.pages.should_stop())
  a=self.pages.open('manual-browser');self.pages.end(a);self.now=604;self.assertTrue(self.pages.should_stop())
 def test_browser_launch_failure_does_not_leave_orphan_process(self):
  self.now=31;self.assertTrue(self.pages.should_stop())
 def test_count_tracks_documents_without_duplicate_streams(self):
  a=self.pages.open('a');b=self.pages.open('b');again=self.pages.open('a')
  self.assertEqual(self.pages.snapshot()['count'],2)
  self.pages.end(a);self.assertEqual(self.pages.snapshot()['count'],2)
  self.pages.end(again);self.assertEqual(self.pages.snapshot()['count'],1)
 def test_explicit_exit_stops_even_with_other_pages_open(self):
  self.pages.open('a');self.pages.open('b');self.pages.request_exit()
  self.assertTrue(self.pages.snapshot()['stopping'])
  self.now=1;self.assertFalse(self.pages.should_stop())
  self.now=2;self.assertTrue(self.pages.should_stop())

class UiRoutesTests(unittest.TestCase):
 def setUp(self):
  self.previous=UI.SESSION;UI.SESSION=PageSessions(startup_grace=None)
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
 def tearDown(self):
  UI.SESSION.stopped.set();self.server.shutdown();self.server.server_close();self.thread.join();UI.SESSION=self.previous
 def request(self,method,path,origin=None):
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
  headers={'Content-Type':'application/json'}
  if origin:headers['Origin']=origin
  c.request(method,path,'{}' if method=='POST' else None,headers)
  r=c.getresponse();raw=r.read();c.close();return r.status,json.loads(raw)
 def test_status_count_and_exit_routes_work_with_open_pages(self):
  UI.SESSION.open('one');UI.SESSION.open('two')
  st,data=self.request('GET','/api/ui/status');self.assertEqual(st,200);self.assertEqual(data['count'],2)
  st,data=self.request('POST','/api/ui/exit');self.assertEqual(st,200);self.assertTrue(data['ok'])
  self.assertTrue(UI.SESSION.snapshot()['stopping'])
 def test_foreign_page_cannot_exit_the_program(self):
  UI.SESSION.open('one');st,data=self.request('POST','/api/ui/exit','https://example.com')
  self.assertEqual(st,403);self.assertFalse(data['ok']);self.assertFalse(UI.SESSION.snapshot()['stopping'])

if __name__=='__main__':unittest.main()
