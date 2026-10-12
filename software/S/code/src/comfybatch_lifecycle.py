"""One lifetime for the workbench, atlas and compare browser documents.

An open stream represents an open document, including a hidden or minimized one.
Explicit pagehide beacons accelerate closure; broken-stream detection also covers
closing the browser process. A short grace period allows reloads and reconnects.
"""
from __future__ import annotations
import json
import threading
import time
import uuid
from urllib.parse import parse_qs, urlparse

SESSION = None

BROWSER_SCRIPT = r"""(()=>{
  // Embedded modules belong to their outer S page, not additional windows.
  if(location.protocol==='file:'||window.top!==window||window.__sUiLifetime)return;
  window.__sUiLifetime=true;
  const id=(crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random());
  let stream=null;
  let exiting=false;
  let controls=document.getElementById('sUiControls');
  if(!controls){
    controls=document.createElement('div');controls.id='sUiControls';
    controls.style.cssText='display:flex;align-items:center;justify-content:flex-end;gap:12px;padding:8px 16px;background:#19202b;color:#fff;font:14px system-ui';
    document.body.prepend(controls);
  }
  const label=document.createElement('span');label.id='sUiCount';label.textContent='正在统计打开的页面';
  const button=document.createElement('button');button.id='sUiExit';button.textContent='退出 S';
  button.style.cssText='padding:6px 12px;border:1px solid #b64040;border-radius:7px;background:#8c2828;color:#fff;cursor:pointer';
  button.title='停止整个 S 程序，所有页面都会断开连接';
  controls.append(label,button);
  function show(state){
    if(state.stopping){exiting=true;label.textContent='S 正在退出，请关闭此页面';button.disabled=true;if(stream){stream.close();stream=null;}return;}
    label.textContent='当前打开 '+state.count+' 个页面'+(state.count===1?' · 这是最后一个':'');
    label.title='按浏览器窗口或标签页统计；S 内嵌的图鉴和对比不重复计算';
  }
  function open(){
    if(stream||exiting)return;
    stream=new EventSource('/api/ui/events?client_id='+encodeURIComponent(id));
    stream.addEventListener('pages',event=>{try{show(JSON.parse(event.data));}catch(e){}});
    stream.onerror=()=>{if(!exiting)label.textContent='连接已断开，正在重连';};
  }
  button.onclick=async()=>{
    button.disabled=true;label.textContent='正在退出 S…';
    try{
      const response=await fetch('/api/ui/exit',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      if(!response.ok)throw Error('退出失败');
      show({stopping:true});
    }catch(e){button.disabled=false;label.textContent='退出未完成，请重试';}
  };
  function close(){
    if(stream){stream.close();stream=null;}
    const body=new Blob([JSON.stringify({client_id:id})],{type:'text/plain'});
    navigator.sendBeacon('/api/ui/close',body);
  }
  addEventListener('pagehide',close);
  addEventListener('pageshow',open);
  open();
})();"""

class PageSessions:
    def __init__(self, *, clock=time.monotonic, close_grace=4, startup_grace=45):
        self.clock=clock
        self.close_grace=close_grace
        self.startup_grace=startup_grace
        self.started=clock()
        self._lock=threading.Lock()
        self._pages={}
        self._seen=False
        self._empty_since=None
        self._exit_at=None
        self.stopped=threading.Event()

    def open(self, page_id):
        with self._lock:
            if self.stopped.is_set() or len(self._pages)>=32:
                return None
            token=uuid.uuid4().hex
            self._pages[token]=page_id
            self._seen=True
            self._empty_since=None
            return token

    def end(self, token):
        with self._lock:
            if token in self._pages:
                del self._pages[token]
                if not self._pages:self._empty_since=self.clock()

    def close(self, page_id):
        with self._lock:
            for token in [k for k,v in self._pages.items() if v==page_id]:
                del self._pages[token]
            if not self._pages and self._seen and self._empty_since is None:
                self._empty_since=self.clock()

    def contains(self, token):
        with self._lock:return token in self._pages

    def snapshot(self):
        with self._lock:
            return {'count':len(set(self._pages.values())),
                    'stopping':self._exit_at is not None or self.stopped.is_set()}

    def request_exit(self):
        with self._lock:
            if self._exit_at is None:self._exit_at=self.clock()

    def should_stop(self):
        with self._lock:
            return self._due()

    def _due(self):
        if self._exit_at is not None:return self.clock()-self._exit_at>=1.25
        if self._pages:return False
        if self._seen:
            return self._empty_since is not None and self.clock()-self._empty_since>=self.close_grace
        return self.startup_grace is not None and self.clock()-self.started>=self.startup_grace

    def watch(self, shutdown):
        def run():
            while not self.stopped.wait(.25):
                if self.should_stop():
                    # Check again under the lock before claiming shutdown.
                    with self._lock:
                        if not self._due():continue
                        self.stopped.set()
                    shutdown()
                    return
        t=threading.Thread(target=run,name='s-ui-lifetime',daemon=True)
        t.start()
        return t

def handle_get(handler, path, query):
    if path=='/api/ui/status':
        value=SESSION.snapshot() if SESSION else {'count':0,'stopping':False}
        value['managed']=SESSION is not None
        body=json.dumps(value).encode('utf8')
        handler.send_response(200)
        handler.send_header('Content-Type','application/json')
        handler.send_header('Content-Length',str(len(body)))
        handler.send_header('Cache-Control','no-store')
        handler.end_headers();handler.wfile.write(body)
        return True
    if path=='/ui_lifecycle.js':
        body=BROWSER_SCRIPT.encode('utf8')
        handler.send_response(200)
        handler.send_header('Content-Type','application/javascript; charset=utf-8')
        handler.send_header('Content-Length',str(len(body)))
        handler.send_header('Cache-Control','no-store')
        handler.end_headers();handler.wfile.write(body)
        return True
    if path!='/api/ui/events':return False
    page_id=parse_qs(query).get('client_id',[''])[0]
    session=SESSION
    token=session.open(page_id) if session and page_id else None
    if not token:
        handler.send_response(204);handler.end_headers();return True
    try:
        handler.connection.settimeout(5)
        handler.send_response(200)
        handler.send_header('Content-Type','text/event-stream')
        handler.send_header('Cache-Control','no-store')
        handler.end_headers()
        previous=None
        while not session.stopped.is_set() and session.contains(token):
            state=json.dumps(session.snapshot())
            frame=('event: pages\ndata: '+state+'\n\n').encode('utf8') if state!=previous else b': open\n\n'
            handler.wfile.write(frame);handler.wfile.flush()
            previous=state
            if session.stopped.wait(1):break
    except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError,OSError):
        pass
    finally:
        session.end(token)
    return True

def handle_close(handler, path, body):
    if path not in ('/api/ui/close','/api/ui/exit'):return False
    origin=handler.headers.get('Origin')
    permitted=not origin or urlparse(origin).hostname in ('localhost','127.0.0.1','::1')
    if path=='/api/ui/exit':
        if permitted and SESSION:SESSION.request_exit()
        body=json.dumps({'ok':bool(permitted and SESSION)}).encode('utf8')
        handler.send_response(200 if permitted and SESSION else 403)
        handler.send_header('Content-Type','application/json')
        handler.send_header('Content-Length',str(len(body)))
        handler.end_headers();handler.wfile.write(body)
        return True
    if permitted and SESSION:
        page_id=body.get('client_id')
        if isinstance(page_id,str):SESSION.close(page_id)
    handler.send_response(204 if permitted else 403)
    handler.send_header('Content-Length','0');handler.end_headers()
    return True
