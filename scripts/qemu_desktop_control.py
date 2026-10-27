"""QMP input/screenshot helper. Set QMP_SOCKET and QMP_EVIDENCE explicitly."""
import json,socket,time,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=Path(os.environ.get('QMP_EVIDENCE',str(ROOT/'work/qemu-desktop-control')))
class QMP:
 key_interval=.12
 def __init__(self):
  EVIDENCE.mkdir(parents=True, exist_ok=True)
  self.sock=socket.socket(socket.AF_UNIX); self.sock.settimeout(10); self.sock.connect(os.environ.get('QMP_SOCKET','/tmp/redexos-desktop-qmp.sock')); self.stream=self.sock.makefile('rwb',buffering=0)
  self.stream.readline(); self.call('qmp_capabilities')
 def call(self,command,**args):
  self.stream.write(json.dumps({'execute':command,'arguments':args}).encode()+b'\n')
  while True:
   reply=json.loads(self.stream.readline())
   if 'error' in reply: raise RuntimeError(reply)
   if 'return' in reply:
    with (EVIDENCE/'qmp-actions.jsonl').open('a') as log: log.write(json.dumps({'time':time.time(),'command':command,'arguments':args,'result':reply['return']})+'\n')
    return reply['return']
 def key(self,key):
  keys=key.split('-')
  for down,order in ((True,keys),(False,list(reversed(keys)))):
   for k in order:
    self.call('input-send-event',events=[{'type':'key','data':{'down':down,'key':{'type':'qcode','data':k}}}])
    time.sleep(self.key_interval)
 def text(self,text):
  symbols={'@':'shift-2','#':'shift-3','^':'shift-6','`':'grave_accent','~':'shift-grave_accent',' ':'spc','\n':'ret','-':'minus','_':'shift-minus','.':'dot','/':'slash',':':'shift-semicolon',';':'semicolon',"'":'apostrophe','"':'shift-apostrophe','=':'equal','>':'shift-dot','<':'shift-comma','|':'shift-backslash','(':'shift-9',')':'shift-0','$':'shift-4','!':'shift-1','?':'shift-slash','+':'shift-equal',',':'comma','[':'bracket_left',']':'bracket_right','{':'shift-bracket_left','}':'shift-bracket_right','&':'shift-7','*':'shift-8','%':'shift-5','\\':'backslash'}
  for c in text:
   self.key(symbols.get(c, 'shift-'+c.lower() if c.isupper() else c))
 def move(self,x,y):
  self.call('input-send-event',events=[{'type':'abs','data':{'axis':'x','value':round(x*32767/1023)}},{'type':'abs','data':{'axis':'y','value':round(y*32767/767)}}])
 def button(self,down):
  self.call('input-send-event',events=[{'type':'btn','data':{'button':'left','down':down}}])
 def click(self,x,y):
  self.move(x,y);time.sleep(.3);self.button(True);time.sleep(.2);self.button(False);time.sleep(.3)
 def shot(self,name):
  self.call('screendump',filename=str(EVIDENCE/name),format='png')
if __name__=='__main__':
 import sys
 EVIDENCE.mkdir(parents=True, exist_ok=True)
 q=QMP()
 if sys.argv[1]=='key':
  for k in sys.argv[2:]: q.key(k)
 elif sys.argv[1]=='shot': q.shot(sys.argv[2])
 elif sys.argv[1]=='click': q.click(int(sys.argv[2]), int(sys.argv[3]))
 elif sys.argv[1]=='type':
  q.key_interval=float(os.environ.get('QMP_KEY_INTERVAL', '.025'))
  q.text(Path(sys.argv[2]).read_text())
