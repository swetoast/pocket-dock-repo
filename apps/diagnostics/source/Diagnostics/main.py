#!/usr/bin/env python3
from __future__ import annotations
import ctypes,ctypes.util,math,os,platform,shutil,struct,subprocess,sys,time,zlib
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
APP=Path(__file__).resolve().parent; DATA=APP/'data'; ASSETS=APP/'assets'; FRAME=Path('/tmp/Diagnostics-screen.bmp')
LED_CFG=Path('/mnt/data/dmenu/mculed_attr.ini')
W,H=640,480; IV=0x20;IJ=0x200;FULL=1;POS=0x1FFF0000;ACCEL=2;VSYNC=4;SOFT=1
QUIT=0x100;HAT=0x602;DOWN=0x603;UP=0x604;MENU=13;MENU_HOLD=8
C={'bg':(10,16,28),'panel':(20,31,49),'line':(54,76,103),'blue':(19,72,116),'cyan':(55,221,255),'green':(55,220,145),'yellow':(255,199,73),'red':(255,91,106),'text':(232,240,251),'muted':(141,160,183)}
KNOWN={0:'A',1:'B',2:'Y',3:'X',4:'L1',5:'R1',6:'SELECT',7:'START',8:'MENU HOLD',9:'STICK',10:'L2',11:'R2',13:'MENU',15:'VOL-',16:'VOL+'}
MAIN=[('Input','Controller, analogue and button map'),('Lighting','Joystick RGB ring controls'),('Audio','Detected playback interface'),('Screen','Display pipeline'),('Battery','Live AXP2202 telemetry'),('System','Runtime, power and thermals'),('Storage','Card, mounts and capacity'),('Tests','Grouped guided diagnostics'),('Exit','Return to TF1')]
EFFECTS=['Always mode','Breath light fast','Breathing light medium','Breathing light slow','Simulator Phantom','Joystick chasing light','Monochromatic rainbow','Multicolor Rainbow']
CATS=[('Joystick & input',['Stick drift','Stick range & circularity','D-pad coverage','Button rollover','Button hold & release','Joystick RGB ring']),('Screen',['Motion & tearing','Inversion patterns','Colour uniformity','Black & highlight levels']),('Audio',['Speaker rattle locator','Long audio stability','Channel confirmation']),('Battery & thermals',['Charging transition','Battery discharge monitor','Thermal soak monitor','Telemetry consistency']),('Storage',['Storage writeability','Storage mount state','Storage capacity','Partition inventory','Card information','I/O activity','VFAT recovery readiness','Temporary render safety'])]
FONT_SANS='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
FONT_SANS_BOLD='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FONT_MONO='/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'
FONT_MONO_BOLD='/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf'
def load_font(path,size):
 try:return ImageFont.truetype(path,size)
 except OSError:return ImageFont.load_default()
def font(size,bold=False):return load_font(FONT_SANS_BOLD if bold else FONT_SANS,size)
def mono(size,bold=False):return load_font(FONT_MONO_BOLD if bold else FONT_MONO,size)
F10,F12,F13,F14,F16,F18,F22,F28,F36=font(10),font(12),font(13),font(14),font(16),font(18,1),font(22,1),font(28,1),font(36,1)
M10,M12,M14,M16,M18=mono(10),mono(12),mono(14),mono(16),mono(18,1)
MARGIN=18;GAP=12;HEADER_H=60;CONTENT_TOP=74;CONTENT_BOTTOM=434;FOOTER_Y=446
ICON_NAMES=['input','audio','screen','battery','system','storage','tests','exit','waveform','temperature','voltage','usb','clock','folder','ok','warning','error','info']
ICON_POS={name:index for index,name in enumerate(ICON_NAMES)}
RESAMPLE_LANCZOS=getattr(getattr(Image,'Resampling',Image),'LANCZOS',getattr(Image,'LANCZOS',Image.BICUBIC))
try: ICON_SHEET=Image.open(ASSETS/'diagnostics-icons.png').convert('RGBA')
except Exception: ICON_SHEET=None
def read(p,d='Unavailable'):
 try:return Path(p).read_text().strip()
 except OSError:return d
def number(value):
 try:return float(value)
 except (TypeError,ValueError):return None
def fmt_voltage(value):
 v=number(value);return f'{v/1000000:.3f} V' if v is not None else 'Unavailable'
def fmt_battery_temp(value):
 v=number(value);return f'{v/10:.1f} °C' if v is not None else 'Unavailable'
def fmt_thermal(value):
 v=number(value);return f'{v/1000:.1f} °C' if v is not None else 'Unavailable'
def fmt_capacity(value):
 v=number(value);return f'{v:.0f}%' if v is not None else 'Unavailable'
def status_colour(value):
 return C['red'] if any(x in str(value).lower() for x in ('failed','missing','mismatch','unavailable','read-only')) else C['green']
def icon_image(name,size,colour=None):
 if ICON_SHEET is None or name not in ICON_POS:return None
 index=ICON_POS[name];left=(index%6)*64;top=(index//6)*64;icon=ICON_SHEET.crop((left,top,left+64,top+64)).resize((size,size),RESAMPLE_LANCZOS)
 if colour:
  alpha=icon.getchannel('A');tint=Image.new('RGBA',icon.size,colour+(0,));tint.putalpha(alpha);icon=tint
 return icon
def draw_icon(image,name,xy,size,colour=None):
 icon=icon_image(name,size,colour)
 if icon is not None:image.paste(icon,xy,icon)
def fit_text(draw,text,font,max_width):
 text=str(text)
 if draw.textlength(text,font=font)<=max_width:return text
 ellipsis='…'
 while text and draw.textlength(text+ellipsis,font=font)>max_width:text=text[:-1]
 return text+ellipsis if text else ellipsis
def center_x(draw,text,font,left,right):return int(left+(right-left-draw.textlength(str(text),font=font))/2)
def head(draw,title,subtitle='',icon=None,image=None,status=None):
 draw.rectangle((0,0,W,HEADER_H),fill=(16,47,80));x=MARGIN
 if icon and image is not None:draw_icon(image,icon,(18,15),28,C['cyan']);x=56
 status_left=W-MARGIN
 if status:
  status=fit_text(draw,status,F12,164);width=draw.textlength(status,font=F12)+22;status_left=W-MARGIN-width;draw.rounded_rectangle((status_left,17,W-MARGIN,43),12,fill=(12,35,59),outline=C['cyan']);draw.text((status_left+11,24),status,font=F12,fill=C['cyan'])
 draw.text((x,8),fit_text(draw,title,F22,max(70,status_left-x-14)),font=F22,fill=C['text'])
 if subtitle:draw.text((x,38),fit_text(draw,subtitle,F12,max(60,status_left-x-14 if status else W-x-MARGIN)),font=F12,fill=C['muted'])
def foot(draw,text):draw.rectangle((0,FOOTER_Y,W,H),fill=(16,47,80));draw.text((MARGIN,456),fit_text(draw,text,F12,W-2*MARGIN),font=F12,fill=C['text'])
def panel(draw,box,title):draw.rounded_rectangle(box,10,fill=C['panel'],outline=C['line'],width=2);draw.text((box[0]+12,box[1]+8),fit_text(draw,title,F12,box[2]-box[0]-24),font=F12,fill=C['cyan'])
def card(image,draw,box,icon,label,value,colour=None,value_font=None):
 value_font=value_font or F14;draw.rounded_rectangle(box,9,fill=C['panel'],outline=C['line'],width=2);draw_icon(image,icon,(box[0]+12,box[1]+max(10,(box[3]-box[1]-24)//2)),24,colour or C['cyan']);text_x=box[0]+46;available=max(24,box[2]-text_x-12);draw.text((text_x,box[1]+12),fit_text(draw,label.upper(),F10,available),font=F10,fill=C['muted']);draw.text((text_x,box[1]+34),fit_text(draw,value,value_font,available),font=value_font,fill=colour or C['text'])
def bar(draw,box,fraction,colour=None):
 fraction=max(0,min(1,fraction));draw.rounded_rectangle(box,6,fill=(29,45,66),outline=C['line']);x=box[0]+int((box[2]-box[0])*fraction)
 if x>box[0]:draw.rounded_rectangle((box[0],box[1],x,box[3]),6,fill=colour or C['cyan'])
def rows(title,sub,values,footer='B: back  ·  Menu Hold: main menu'):
 im=Image.new('RGB',(W,H),C['bg']);d=ImageDraw.Draw(im);head(d,title,sub,image=im);panel(d,(MARGIN,72,W-MARGIN,CONTENT_BOTTOM),'STATUS AND DETAILS');y=106
 for label,value,colour in values[:10]:
  d.text((32,y),fit_text(d,label,F12,205),font=F12,fill=C['muted']);d.text((250,y-2),fit_text(d,value,F14,358),font=F14,fill=colour);d.line((30,y+23,610,y+23),fill=(34,49,68));y+=31
 foot(d,footer);return im
def listing(title,items,sel,footer):
 im=Image.new('RGB',(W,H),C['bg']);d=ImageDraw.Draw(im);head(d,title,'Hardware information and guided checks',icon='tests' if title=='Tests' else 'info',image=im,status=f'{sel+1} OF {len(items)}');start=max(0,min(sel-4,max(0,len(items)-6)));y=68
 menu_icons={'Input':'input','Lighting':'waveform','Audio':'audio','Screen':'screen','Battery':'battery','System':'system','Storage':'storage','Tests':'tests','Exit':'exit','Joystick & input':'input','Battery & thermals':'battery'}
 for i in range(start,min(len(items),start+6)):
  name,desc=items[i] if isinstance(items[i],tuple) else (items[i],'');active=i==sel;box=(MARGIN,y,W-MARGIN,y+55);d.rounded_rectangle(box,10,fill=C['blue'] if active else C['panel'],outline=C['cyan'] if active else C['line'],width=2);draw_icon(im,menu_icons.get(name,'tests'),(30,y+11),34,C['text'] if active else C['muted']);d.text((78,y+7),fit_text(d,name,F16,520),font=F16,fill=C['text']);d.text((78,y+32),fit_text(d,desc,F12,520),font=F12,fill=C['cyan'] if active else C['muted']);y+=60
 foot(d,footer);return im

def load_sdl():
 for n in filter(None,[ctypes.util.find_library('SDL2'),'libSDL2-2.0.so.0']):
  try:return ctypes.CDLL(n)
  except OSError:pass
 raise RuntimeError('SDL2 not found')
def bind(s):
 s.SDL_Init.argtypes=[ctypes.c_uint32];s.SDL_Init.restype=ctypes.c_int
 s.SDL_Quit.argtypes=[];s.SDL_Quit.restype=None
 s.SDL_GetError.argtypes=[];s.SDL_GetError.restype=ctypes.c_char_p
 s.SDL_GetCurrentVideoDriver.argtypes=[];s.SDL_GetCurrentVideoDriver.restype=ctypes.c_char_p
 s.SDL_CreateWindow.argtypes=[ctypes.c_char_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint32];s.SDL_CreateWindow.restype=ctypes.c_void_p
 s.SDL_DestroyWindow.argtypes=[ctypes.c_void_p];s.SDL_DestroyWindow.restype=None
 s.SDL_CreateRenderer.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_uint32];s.SDL_CreateRenderer.restype=ctypes.c_void_p
 s.SDL_DestroyRenderer.argtypes=[ctypes.c_void_p];s.SDL_DestroyRenderer.restype=None
 s.SDL_RWFromFile.argtypes=[ctypes.c_char_p,ctypes.c_char_p];s.SDL_RWFromFile.restype=ctypes.c_void_p
 s.SDL_LoadBMP_RW.argtypes=[ctypes.c_void_p,ctypes.c_int];s.SDL_LoadBMP_RW.restype=ctypes.c_void_p
 s.SDL_FreeSurface.argtypes=[ctypes.c_void_p];s.SDL_FreeSurface.restype=None
 s.SDL_CreateTextureFromSurface.argtypes=[ctypes.c_void_p,ctypes.c_void_p];s.SDL_CreateTextureFromSurface.restype=ctypes.c_void_p
 s.SDL_DestroyTexture.argtypes=[ctypes.c_void_p];s.SDL_DestroyTexture.restype=None
 s.SDL_RenderClear.argtypes=[ctypes.c_void_p];s.SDL_RenderClear.restype=ctypes.c_int
 s.SDL_RenderCopy.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p];s.SDL_RenderCopy.restype=ctypes.c_int
 s.SDL_RenderPresent.argtypes=[ctypes.c_void_p];s.SDL_RenderPresent.restype=None
 s.SDL_PollEvent.argtypes=[ctypes.c_void_p];s.SDL_PollEvent.restype=ctypes.c_int
 s.SDL_Delay.argtypes=[ctypes.c_uint32];s.SDL_Delay.restype=None
 s.SDL_JoystickOpen.argtypes=[ctypes.c_int];s.SDL_JoystickOpen.restype=ctypes.c_void_p
 s.SDL_JoystickClose.argtypes=[ctypes.c_void_p];s.SDL_JoystickClose.restype=None
 s.SDL_JoystickName.argtypes=[ctypes.c_void_p];s.SDL_JoystickName.restype=ctypes.c_char_p

LED_EDIT_FIELDS={'red':27,'green':28,'blue':29,'brightness':30,'background red':42,'background green':43,'background blue':44}
FIELDS=['enabled','effect','red','green','blue','brightness','background red','background green','background blue']
class LED:
 def __init__(self):
  self.words=[];self.raw=b'';self.valid=False;self.integrity_ok=False;self.changed=False;self.status='Unavailable';self.session_original=None;self.reload()
 @staticmethod
 def checksum(raw):return (zlib.crc32(raw[:180])^0x35495BFE)&0xffffffff
 def reload(self):
  self.words=[];self.raw=b'';self.valid=False;self.integrity_ok=False;self.changed=False
  if not LED_CFG.exists():self.status='Configuration missing';return
  try:raw=LED_CFG.read_bytes()
  except OSError as e:self.status=f'Configuration unreadable: {e.errno}';return
  if len(raw)!=184:self.status=f'Invalid size: {len(raw)} bytes';return
  try:words=list(struct.unpack('<46I',raw))
  except struct.error:self.status='Invalid binary structure';return
  self.raw=raw;self.words=words;self.integrity_ok=words[45]==self.checksum(raw);self.valid=self.integrity_ok
  if not self.integrity_ok:self.status='Integrity mismatch - read only';return
  if self.session_original is None:self.session_original=raw
  self.status='Ready'
 @property
 def mode(self):return min(7,self.words[1]) if self.words else 0
 def value(self,key):
  if not self.words:return 0
  if key=='enabled':return self.words[0]
  if key=='effect':return self.mode
  index=LED_EDIT_FIELDS.get(key)
  return self.words[index] if index is not None else 0
 def editable(self,key):return self.valid and key in LED_EDIT_FIELDS
 def set(self,key,val):
  if not self.editable(key):
   if key in ('enabled','effect'):self.status='Effect controls are read only'
   return
  val=max(0,min(255,int(val)));index=LED_EDIT_FIELDS[key]
  if self.words[index]!=val:self.words[index]=val;self.changed=True;self.status='Unsaved changes'
 def _atomic_write(self,payload):
  temp=LED_CFG.with_name('mculed_attr.ini.Diagnostics.tmp')
  try:
   with temp.open('wb') as handle:handle.write(payload);handle.flush();os.fsync(handle.fileno())
   os.replace(temp,LED_CFG)
   try:
    directory_fd=os.open(str(LED_CFG.parent),os.O_RDONLY);os.fsync(directory_fd);os.close(directory_fd)
   except OSError:pass
  finally:
   try:temp.unlink()
   except OSError:pass
 def _save_original_backups(self):
  if self.session_original is None:return
  temp_root=Path('/tmp/Diagnostics');temp_root.mkdir(parents=True,exist_ok=True)
  temp_backup=temp_root/'mculed_attr.original'
  if not temp_backup.exists():temp_backup.write_bytes(self.session_original)
  try:
   DATA.mkdir(parents=True,exist_ok=True);persistent=DATA/'mculed_attr.original'
   if not persistent.exists():
    with persistent.open('wb') as handle:handle.write(self.session_original);handle.flush();os.fsync(handle.fileno())
  except OSError:pass
 def save(self):
  if not self.valid:self.status='Save blocked: configuration not verified';return False
  if not self.changed:self.status='No changes to save';return True
  if not os.access(LED_CFG.parent,os.W_OK):self.status='Save blocked: configuration is read only';return False
  before=LED_CFG.read_bytes()
  self._save_original_backups()
  words=list(self.words);placeholder=bytearray(struct.pack('<46I',*words));words[45]=self.checksum(placeholder);payload=struct.pack('<46I',*words)
  try:
   self._atomic_write(payload)
   written=LED_CFG.read_bytes()
   if written!=payload or len(written)!=184 or struct.unpack_from('<I',written,180)[0]!=self.checksum(written):raise OSError('verification failed')
  except Exception:
   try:self._atomic_write(before)
   except Exception:self.status='Save failed; automatic restore also failed';return False
   self.words=list(struct.unpack('<46I',before));self.raw=before;self.changed=False;self.status='Save failed; original restored';return False
  self.words=words;self.raw=payload;self.changed=False;self.integrity_ok=True;self.valid=True;self.status='Saved - reopen TF1 LED settings to apply';return True

class State:
 def __init__(self,s,j):
  self.s=s;self.j=j;self.page='menu';self.sel=0;self.cat=0;self.item=0;self.field=0;self.running=True;self.held=set();self.seen=set();self.unknown=set();self.down_at={};self.hold_fired=set();self.hat=0;self.axes={0:0,1:0};self.buf=b'';self.last='Ready';self.tele={};self.tele_at=0;self.test='Ready';self.cover=set();self.test_started=0.0;self.samples=[];self.result={};self.max_held=0;self.press_seen=set();self.release_seen=set();self.io_start=None;self.screen_phase=0;self.sample_at=0.0;self.test_armed=False;self.activation_button=None;self.renderer_name='Unknown';self.video_driver='Unknown';self.led_return='menu';self.led=LED();self.worker=None
  self.name=(s.SDL_JoystickName(j) or b'unknown').decode(errors='replace')
  try:self.js=os.open('/dev/input/js0',os.O_RDONLY|os.O_NONBLOCK)
  except OSError:self.js=None
 def poll(self):
  ch=False
  if self.js is not None:
   try:
    while True:self.buf+=os.read(self.js,256)
   except BlockingIOError:pass
   except OSError:pass
   while len(self.buf)>=8:
    q,self.buf=self.buf[:8],self.buf[8:];_,v,t,n=struct.unpack('<IhBB',q)
    if t&0x7f==2:self.axes[n]=v;ch=True
  if time.monotonic()-self.tele_at>1:
   b='/sys/class/power_supply/axp2202-battery';u='/sys/class/power_supply/axp2202-usb';z='/sys/class/thermal';self.tele={k:read(v) for k,v in {'capacity':b+'/capacity','status':b+'/status','health':b+'/health','voltage':b+'/voltage_now','btemp':b+'/temp','usb':u+'/online','cpu':z+'/thermal_zone0/temp','gpu':z+'/thermal_zone1/temp'}.items()};self.tele_at=time.monotonic();ch=True
  return ch
def input_page(st):
 im=Image.new('RGB',(W,H),C['bg']);d=ImageDraw.Draw(im);head(d,'Input','Live controller map')
 panel(d,(14,68,398,350),'BUTTONS AND D-PAD');panel(d,(412,68,626,350),'ANALOGUE')
 buttons=[(28,106,'L2',10),(98,106,'L1',4),(244,106,'R1',5),(314,106,'R2',11),(230,175,'Y',2),(279,143,'X',3),(328,175,'A',0),(279,207,'B',1),(18,291,'SELECT',6),(87,291,'MENU',13),(156,291,'M HOLD',8),(225,291,'START',7),(294,291,'STICK',9),(28,320,'VOL-',15),(314,320,'VOL+',16)]
 for x,y,label,button in buttons:
  active=button in st.held;d.rounded_rectangle((x,y,x+62,y+25),6,fill=C['green'] if active else (36,52,72),outline=C['cyan'] if active else C['line']);d.text((x+5,y+5),label,font=F12,fill=C['text'])
 cx,cy=124,210
 for dx,dy,label,value in [(0,-37,'U',1),(37,0,'R',2),(0,37,'D',4),(-37,0,'L',8)]:
  active=bool(st.hat&value);d.rounded_rectangle((cx+dx-15,cy+dy-15,cx+dx+15,cy+dy+15),5,fill=C['green'] if active else (36,52,72),outline=C['cyan'] if active else C['line']);d.text((cx+dx-4,cy+dy-7),label,font=F12,fill=C['text'])
 px,py,r=519,192,70;d.ellipse((px-r,py-r,px+r,py+r),fill=(13,23,37),outline=C['green'] if 9 in st.held else C['line'],width=3);d.line((px-r,py,px+r,py),fill=C['line']);d.line((px,py-r,px,py+r),fill=C['line'])
 ax=st.axes.get(0,0);ay=st.axes.get(1,0);sx=max(-r,min(r,ax/32768*r));sy=max(-r,min(r,ay/32768*r));d.ellipse((px+sx-8,py+sy-8,px+sx+8,py+sy+8),fill=C['cyan'])
 d.text((438,284),f'X {ax:6d}',font=M14,fill=C['text']);d.text((533,284),f'Y {ay:6d}',font=M14,fill=C['text']);d.text((438,315),f'Hat {st.hat:2d}',font=M14,fill=C['text'])
 panel(d,(14,362,626,438),'LIVE INPUT');d.text((30,389),fit_text(d,st.last,F14,578),font=F14,fill=C['yellow'])
 held=', '.join(KNOWN.get(x,str(x)) for x in sorted(st.held)) or 'None';unknown=', '.join(str(x) for x in sorted(st.unknown)) or 'None';d.text((30,414),fit_text(d,'Held: '+held,F12,370),font=F12,fill=C['text']);d.text((420,414),fit_text(d,'Unknown: '+unknown,F12,185),font=F12,fill=C['red'] if st.unknown else C['muted'])
 foot(d,'Menu 13 is testable  ·  Menu Hold 8: main menu  ·  Hold A: Lighting');return im

def led_page(st):
 l=st.led;im=Image.new('RGB',(W,H),C['bg']);d=ImageDraw.Draw(im);head(d,'Lighting',l.status)
 fg=(l.value('red'),l.value('green'),l.value('blue'));bg=(l.value('background red'),l.value('background green'),l.value('background blue'))
 panel(d,(16,66,624,126),'LIVE CONFIGURATION PREVIEW')
 d.text((30,94),'Foreground',font=F12,fill=C['muted']);d.rounded_rectangle((112,86,210,116),6,fill=fg,outline=C['line']);d.text((220,94),f'{fg[0]}, {fg[1]}, {fg[2]}',font=F12,fill=C['text'])
 d.text((350,94),'Background',font=F12,fill=C['muted']);d.rounded_rectangle((430,86,528,116),6,fill=bg,outline=C['line']);d.text((538,94),f'{bg[0]}, {bg[1]}, {bg[2]}',font=F12,fill=C['text'])
 y=132
 for i,key in enumerate(FIELDS):
  on=i==st.field;v=l.value(key);editable=l.editable(key);d.rounded_rectangle((18,y,622,y+31),6,fill=C['blue'] if on else C['panel'],outline=C['cyan'] if on else C['line'])
  label=key.title()+('' if editable else '  · read only');d.text((31,y+7),label,font=F12,fill=C['text'] if editable else C['muted'])
  if key=='enabled':d.text((540,y+7),'On' if v else 'Off',font=F12,fill=C['muted'])
  elif key=='effect':d.text((356,y+7),EFFECTS[v],font=F12,fill=C['muted'])
  else:
   x1,x2=292,548;bar_y=y+11;fill_x=x1+int((x2-x1)*v/255);d.rounded_rectangle((x1,bar_y,x2,bar_y+10),5,fill=(36,52,72),outline=C['line'])
   if fill_x>x1:d.rounded_rectangle((x1,bar_y,fill_x,bar_y+10),5,fill=C['cyan'] if on else C['green'])
   d.ellipse((max(x1,fill_x-5),bar_y-2,min(x2,fill_x+5),bar_y+12),fill=C['text']);d.text((565,y+7),f'{v:3d}',font=M12,fill=C['cyan'] if on else C['text'])
  y+=34
 foot(d,'Up/Down select  ·  Left/Right ±1  ·  L1/R1 ±10  ·  L2 0  ·  R2 255  ·  A save  ·  B back');return im

def audio_overview(st):
 im=Image.new('RGB',(W,H),C['bg']);d=ImageDraw.Draw(im);head(d,'Audio','Single speaker output and playback diagnostics','audio',im,'READY');panel(d,(18,74,622,270),'PLAYBACK MONITOR')
 points=[]
 for x in range(42,598,4):
  phase=(x-42)/22;amplitude=30*(0.35+0.65*math.sin((x-42)/90)**2);points.append((x,172+math.sin(phase)*amplitude))
 d.line(points,fill=C['cyan'],width=3);d.text((278,224),'IDLE',font=F14,fill=C['muted'])
 card(im,d,(18,286,210,374),'audio','Output','audiocodec');card(im,d,(224,286,416,374),'waveform','Format','48 kHz · 16-bit',value_font=M14);card(im,d,(430,286,622,374),'info','Playback','SDL2 queued')
 d.rounded_rectangle((18,387,622,434),9,fill=C['panel'],outline=C['line']);draw_icon(im,'waveform',(34,397),26,C['cyan']);d.text((70,397),'Sweep',font=F14,fill=C['text']);draw_icon(im,'clock',(221,397),26,C['cyan']);d.text((257,397),'Stability',font=F14,fill=C['text']);draw_icon(im,'tests',(420,397),26,C['cyan']);d.text((456,397),'Sequence',font=F14,fill=C['text']);foot(d,'A: Audio tests  ·  B: back  ·  Menu Hold: main menu');return im
def screen_overview(st):
 im=Image.new('RGB',(W,H),C['bg']);d=ImageDraw.Draw(im);head(d,'Screen','Active display pipeline','screen',im,st.renderer_name.upper());panel(d,(18,74,386,348),'DISPLAY PREVIEW');d.rounded_rectangle((42,104,362,315),8,fill=(5,10,18),outline=C['cyan'],width=2);d.line((202,116,202,302),fill=C['line']);d.line((54,209,350,209),fill=C['line']);
 for i,c in enumerate(((240,70,80),(60,210,125),(60,130,240))):d.rectangle((68+i*58,134,112+i*58,174),fill=c)
 for i,v in enumerate((20,55,95,140,190,235)):d.rectangle((68+i*42,246,105+i*42,276),fill=(v,v,v))
 d.text((281,286),'4:3',font=F14,fill=C['muted']);card(im,d,(402,74,622,151),'screen','Resolution','640 × 480',value_font=M14);card(im,d,(402,161,622,238),'info','Video driver',st.video_driver);card(im,d,(402,248,622,325),'system','Renderer',st.renderer_name)
 d.rounded_rectangle((18,362,622,434),9,fill=C['panel'],outline=C['line']);labels=[('waveform','Motion'),('info','Inversion'),('screen','Uniformity'),('battery','Levels')]
 for i,(ico,label) in enumerate(labels):x=32+i*148;draw_icon(im,ico,(x,380),28,C['cyan']);d.text((x+36,386),label,font=F12,fill=C['text'])
 foot(d,'A: Screen tests  ·  B: back  ·  Menu Hold: main menu');return im
def battery_overview(st):
 data=st.tele;capacity=number(data.get('capacity'));fraction=(capacity or 0)/100;status=data.get('status','Unavailable');im=Image.new('RGB',(W,H),C['bg']);d=ImageDraw.Draw(im);head(d,'Battery','AXP2202 power telemetry','battery',im,status.upper());panel(d,(18,74,248,378),'CAPACITY');d.rounded_rectangle((80,115,185,315),14,fill=(12,22,37),outline=C['cyan'],width=4);d.rectangle((111,102,154,117),fill=C['cyan']);fill_top=306-int(184*fraction);
 if capacity is not None and fraction>0:d.rounded_rectangle((89,fill_top,176,306),8,fill=C['cyan']);
 capacity_text=fmt_capacity(data.get('capacity'));capacity_font=M18 if capacity is not None else F16;d.text((center_x(d,capacity_text,capacity_font,18,248),330 if capacity is not None else 342),capacity_text,font=capacity_font,fill=C['text'] if capacity is not None else C['muted'])
 card(im,d,(264,74,622,153),'voltage','Voltage',fmt_voltage(data.get('voltage')),value_font=M16);card(im,d,(264,163,622,242),'temperature','Temperature',fmt_battery_temp(data.get('btemp')),value_font=M16);card(im,d,(264,252,438,331),'ok','Health',data.get('health','Unavailable'));card(im,d,(448,252,622,331),'usb','USB power','Connected' if data.get('usb')=='1' else 'Disconnected')
 d.rounded_rectangle((18,390,622,434),9,fill=C['panel'],outline=C['line']);d.text((36,403),'Running on USB power' if data.get('usb')=='1' else 'Running on battery',font=F16,fill=C['cyan']);foot(d,'A: Battery tests  ·  B: back  ·  Menu Hold: main menu');return im
def system_overview(st):
 cpu=number(st.tele.get('cpu'));gpu=number(st.tele.get('gpu'));im=Image.new('RGB',(W,H),C['bg']);d=ImageDraw.Draw(im);head(d,'System','Runtime and thermal telemetry','system',im,'LIVE');panel(d,(18,74,622,240),'PROCESSOR TEMPERATURES')
 for y,label,value in ((112,'CPU',cpu),(178,'GPU',gpu)):
  draw_icon(im,'system',(36,y-10),34,C['cyan']);d.text((82,y),label,font=F16,fill=C['text']);shown=fmt_thermal(value);d.text((504,y),shown,font=M16,fill=C['text']);bar(d,(128,y+4,480,y+20),min(1,(value or 0)/90000))
 card(im,d,(18,256,310,331),'system','Architecture',platform.machine());card(im,d,(330,256,622,331),'info','Kernel',platform.release());card(im,d,(18,345,310,420),'info','Python',platform.python_version());uptime=read('/proc/uptime','Unavailable').split()[0];uptime=f'{int(float(uptime))//3600} h {(int(float(uptime))%3600)//60} min' if uptime!='Unavailable' else uptime;card(im,d,(330,345,622,420),'clock','Uptime',uptime);foot(d,'B: back  ·  Menu Hold: main menu');return im
def storage_overview(st):
 device,filesystem,options=mount_info();total,free=storage_usage();used=(total-free) if total is not None else None;fraction=used/total if total else 0;mode='Read-write' if 'rw' in options.split(',') else 'Read-only' if options!='Unavailable' else 'Unavailable';im=Image.new('RGB',(W,H),C['bg']);d=ImageDraw.Draw(im);head(d,'Storage','TF1 card and filesystem','storage',im,mode.upper());panel(d,(18,74,622,168),'CARD CAPACITY');bar(d,(36,112,604,136),fraction);d.text((36,143),f'{fraction*100:.0f}% used' if total else 'Unavailable',font=M12,fill=C['muted']);free_text=f'{free/1073741824:.2f} GiB free' if free is not None else 'Unavailable';d.text((604-d.textlength(free_text,font=M12),143),free_text,font=M12,fill=C['text'])
 draw_icon(im,'storage',(52,208),108,C['cyan']);d.text((64,318),'microSD',font=F16,fill=C['text']);card(im,d,(194,190,622,262),'storage','Device',device);card(im,d,(194,272,622,344),'folder','Mount','/mnt/mmc');card(im,d,(194,354,400,426),'info','Filesystem',filesystem);card(im,d,(414,354,622,426),'ok','State',mode,status_colour(mode));foot(d,'A: Storage tests  ·  B: back  ·  Menu Hold: main menu');return im
def overview(st,name):return {'audio':audio_overview,'screen':screen_overview,'battery':battery_overview,'system':system_overview,'storage':storage_overview}[name](st)

def current_test(st):return CATS[st.cat][1][st.item]
def elapsed(st):return time.monotonic()-st.test_started if st.test_started else 0.0
def progress_bar(d,fraction,y=414):
 fraction=max(0.0,min(1.0,fraction));d.rounded_rectangle((35,y,605,y+16),8,fill=(36,52,72),outline=C['line']);d.rounded_rectangle((35,y,35+int(570*fraction),y+16),8,fill=C['cyan']) if fraction else None;label=f'{fraction*100:.0f}%';d.text((center_x(d,label,M10,35,605),y+2),label,font=M10,fill=C['text'])
def mount_info(path='/mnt/mmc'):
 try:
  for line in Path('/proc/mounts').read_text().splitlines():
   parts=line.split()
   if len(parts)>=4 and parts[1]==path:return parts[0],parts[2],parts[3]
 except OSError:pass
 return 'Unavailable','Unavailable','Unavailable'
def storage_usage():
 try:
  v=os.statvfs('/mnt/mmc');total=v.f_frsize*v.f_blocks;free=v.f_frsize*v.f_bavail;return total,free
 except OSError:return None,None
def io_values():
 try:
  values=Path('/sys/block/mmcblk0/stat').read_text().split();return tuple(int(x) for x in values)
 except (OSError,ValueError):return None
def test_writeability():
 path=Path(f'/mnt/mmc/.Diagnostics-write-test-{os.getpid()}')
 try:
  payload=b'Diagnostics storage verification\n';
  with path.open('wb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
  ok=path.read_bytes()==payload;path.unlink();return 'Passed' if ok else 'Verification failed'
 except OSError as e:
  try:path.unlink()
  except OSError:pass
  return f'Failed: errno {e.errno}'
def start_test(st):
 name=current_test(st);st.test='Running';st.test_started=time.monotonic();st.sample_at=0.0;st.samples=[];st.result={};st.cover=set();st.max_held=0;st.press_seen=set();st.release_seen=set();st.io_start=None;st.test_armed=False;st.activation_button=None
 st.result['Initial USB']=st.tele.get('usb','Unavailable');st.result['Initial status']=st.tele.get('status','Unavailable');st.result['Initial capacity']=st.tele.get('capacity','Unavailable');st.result['Initial voltage']=st.tele.get('voltage','Unavailable')
 if name=='Storage writeability':st.result={'Result':test_writeability()};st.test='Completed'
 elif name=='Storage mount state':
  dev,fs,opts=mount_info();st.result={'Device':dev,'Filesystem':fs,'Mode':'Read-write' if 'rw' in opts.split(',') else 'Read-only','Options':opts};st.test='Completed'
 elif name=='Storage capacity':
  total,free=storage_usage();st.result={'Total':f'{total/1073741824:.2f} GiB' if total is not None else 'Unavailable','Available':f'{free/1073741824:.2f} GiB' if free is not None else 'Unavailable'};st.test='Completed'
 elif name=='Partition inventory':
  parts=sorted(x.name for x in Path('/sys/class/block').glob('mmcblk0p*'));st.result={'Partitions':', '.join(parts) or 'None','Count':len(parts)};st.test='Completed'
 elif name=='Card information':st.result={'Device':'/dev/mmcblk0','Name':read('/sys/block/mmcblk0/device/name'),'CID':read('/sys/block/mmcblk0/device/cid'),'Kernel read only':read('/sys/block/mmcblk0/ro')};st.test='Completed'
 elif name=='I/O activity':st.io_start=io_values()
 elif name=='VFAT recovery readiness':st.result={'fsck.vfat':shutil.which('fsck.vfat') or 'Unavailable','Mount options':mount_info()[2],'Device':'/dev/mmcblk0p1'};st.test='Completed'
 elif name=='Temporary render safety':
  probe=Path(f'/tmp/.Diagnostics-render-test-{os.getpid()}')
  try:probe.write_bytes(b'ok');ok=probe.read_bytes()==b'ok';st.result={'/tmp write/read':'Passed' if ok else 'Failed','Frame path':str(FRAME)}
  except OSError as exc:st.result={'/tmp write/read':f'Failed: errno {exc.errno}'}
  finally:
   try:probe.unlink()
   except OSError:pass
  st.test='Completed'
 elif name in ('Speaker rattle locator','Long audio stability','Channel confirmation'):
  mode={'Speaker rattle locator':'rattle','Long audio stability':'stability','Channel confirmation':'stereo'}[name];stop_worker(st);st.worker=subprocess.Popen([sys.executable,str(APP/'audio_worker.py'),mode],cwd=APP,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def stop_worker(st):
 if st.worker and st.worker.poll() is None:
  st.worker.terminate()
  try:st.worker.wait(timeout=1)
  except subprocess.TimeoutExpired:st.worker.kill();st.worker.wait()
 st.worker=None
def stick_metrics(samples):
 if not samples:return {'samples':0,'max_radius':0,'sectors':0,'circularity':0}
 radii=[math.hypot(x,y) for x,y in samples];sector=set();sector_max=[0.0]*16
 for (x,y),radius in zip(samples,radii):
  if radius>2000:
   angle=(math.atan2(y,x)+2*math.pi)%(2*math.pi);index=int((angle+math.pi/16)/(2*math.pi)*16)%16;sector.add(index);sector_max[index]=max(sector_max[index],radius)
 active=[v for v in sector_max if v>0];circ=(min(active)/max(active)*100) if len(active)>=4 and max(active)>0 else 0
 return {'samples':len(samples),'max_radius':max(radii),'sectors':len(sector),'circularity':circ}
def update_test(st):
 if st.page!='test' or st.test!='Running':return False
 name=current_test(st);now=elapsed(st)
 if name in ('Stick drift','Stick range & circularity') and time.monotonic()>=st.sample_at:
  st.samples.append((st.axes.get(0,0),st.axes.get(1,0)));st.sample_at=time.monotonic()+0.05
  if name=='Stick drift' and now>=5:st.test='Completed'
 elif name=='Button rollover':st.max_held=max(st.max_held,len(st.held))
 elif name=='I/O activity' and now>=5:
  end=io_values();st.result={'Activity':'Detected' if st.io_start and end and end!=st.io_start else 'No change','Elapsed':'5.0 s'};st.test='Completed'
 elif name in ('Charging transition','Battery discharge monitor','Thermal soak monitor','Telemetry consistency'):
  for key in ('capacity','voltage','btemp','cpu','gpu'):
   value=number(st.tele.get(key))
   if value is not None:
    low,high=st.result.get(key,(value,value));st.result[key]=(min(low,value),max(high,value))
  if name=='Charging transition' and (st.tele.get('usb')!=st.result.get('Initial USB') or st.tele.get('status')!=st.result.get('Initial status')):st.result['Transition']='Detected'
 elif name in ('Speaker rattle locator','Long audio stability','Channel confirmation') and st.worker and st.worker.poll() is not None:
  messages={0:'Completed',2:'SDL audio initialisation failed',3:'audiocodec not found',4:'Audio device open failed',5:'Audio queue failed',6:'Playback timed out'};st.test=messages.get(st.worker.returncode,f'Failed ({st.worker.returncode})');st.worker=None
 return True
def checker_pattern(size):
 im=Image.new('RGB',(W,H));pixels=im.load()
 for y in range(H):
  row=(y//size)&1
  for x in range(W):pixels[x,y]=(255,255,255) if (((x//size)&1)^row) else (0,0,0)
 return im
INVERSION_PATTERNS={1:checker_pattern(1),2:checker_pattern(2)}
def screen_test_page(st,name):
 phase=time.monotonic()
 if name=='Inversion patterns':im=INVERSION_PATTERNS[1 if st.screen_phase%2==0 else 2].copy();d=ImageDraw.Draw(im)
 else:im=Image.new('RGB',(W,H),'black');d=ImageDraw.Draw(im)
 if name=='Motion & tearing':
  x=int((phase*180)%(W+100))-50;d.rectangle((x,0,x+50,H),fill='white');d.rectangle(((x+W//2)%W,0,(x+W//2)%W+8,H),fill='cyan')
 elif name=='Colour uniformity':
  colours=['red','green','blue','white','gray'];colour=colours[st.screen_phase%len(colours)];d.rectangle((0,0,W,H),fill=colour);d.line((W//2,0,W//2,H),fill='cyan');d.line((0,H//2,W,H//2),fill='cyan')
 elif name=='Black & highlight levels':
  levels=[0,4,8,12,16,24,32,64,128,192,223,239,247,251,255]
  for i,value in enumerate(levels):d.rectangle((i*W//len(levels),0,(i+1)*W//len(levels),H),fill=(value,value,value))
 d.rounded_rectangle((10,10,630,50),7,fill='black',outline='white');d.text((22,18),name,font=F18,fill='white');d.rounded_rectangle((10,435,630,473),7,fill='black',outline='white');d.text((22,445),'A: change pattern  ·  B: back  ·  Menu Hold: category',font=F12,fill='white');return im

def test_page(st):
 name=current_test(st)
 if st.cat==1:return screen_test_page(st,name)
 if name=='Joystick RGB ring':
  l=st.led;return rows(name,'Compare saved TF1 configuration with the physical ring',[('Integrity','Verified' if l.integrity_ok else l.status,status_colour(l.status)),('Effect',EFFECTS[l.mode] if l.words else 'Unavailable',C['text']),('Foreground',f'{l.value("red")}, {l.value("green")}, {l.value("blue")}',C['text']),('Background',f'{l.value("background red")}, {l.value("background green")}, {l.value("background blue")}',C['text']),('Lighting editor','Main menu > Lighting',C['cyan'])],'B: back  ·  Menu Hold: category')
 if name=='D-pad coverage':
  checks=[(1,'Up'),(3,'Up + Right'),(2,'Right'),(6,'Down + Right'),(4,'Down'),(12,'Down + Left'),(8,'Left'),(9,'Up + Left')];return rows(name,st.test,[(label,'Seen' if value in st.cover else 'Waiting',C['green'] if value in st.cover else C['muted']) for value,label in checks],'A: reset  ·  B: back  ·  Menu Hold: category')
 if name in ('Stick drift','Stick range & circularity'):
  xs=[x for x,y in st.samples] or [st.axes.get(0,0)];ys=[y for x,y in st.samples] or [st.axes.get(1,0)];metrics=stick_metrics(st.samples);data=[('Status',st.test,C['green']),('Elapsed',f'{elapsed(st):.1f} s',C['cyan']),('Current',f'{st.axes.get(0,0)}, {st.axes.get(1,0)}',C['text']),('X range',f'{min(xs)} .. {max(xs)}',C['yellow']),('Y range',f'{min(ys)} .. {max(ys)}',C['yellow']),('Maximum radius',f'{metrics["max_radius"]:.0f}',C['text']),('Angular sectors',f'{metrics["sectors"]} / 16',C['text']),('Circularity',f'{metrics["circularity"]:.1f}%',C['green'] if metrics['circularity']>=80 else C['yellow']),('Samples',metrics['samples'],C['text'])];im=rows(name,'Fixed-rate analogue measurement',data,'A: start / finish  ·  B: back  ·  Menu Hold: category');progress_bar(ImageDraw.Draw(im),min(1,elapsed(st)/5) if name=='Stick drift' else metrics['sectors']/16,414);return im
 if name=='Button rollover':return rows(name,st.test,[('Currently held',len(st.held),C['text']),('Maximum held',st.max_held,C['green']),('Controls',', '.join(KNOWN.get(x,str(x)) for x in sorted(st.held)) or 'None',C['yellow']),('Activation','Starts after A is released',C['muted'])],'A: reset  ·  B: back  ·  Menu Hold: category')
 if name=='Button hold & release':return rows(name,st.test,[('Pressed',len(st.press_seen),C['cyan']),('Released',len(st.release_seen),C['green']),('Still held',', '.join(KNOWN.get(x,str(x)) for x in sorted(st.held)) or 'None',C['yellow']),('Last event',st.last,C['text']),('Activation','Starts after A is released',C['muted'])],'A: reset  ·  B: back  ·  Menu Hold: category')
 if st.cat==2:return rows(name,'Isolated audiocodec worker',[('Status',st.test,status_colour(st.test)),('Elapsed',f'{elapsed(st):.1f} s',C['cyan']),('Requested device','audiocodec',C['text']),('Instruction','Listen for clean expected output',C['yellow'])],'A: run / restart  ·  B: stop / back  ·  Menu Hold: category')
 if st.cat==3:
  b=st.tele;data=[('Status',st.test,C['green']),('Elapsed',f'{elapsed(st):.1f} s',C['cyan']),('USB power','Connected' if b.get('usb')=='1' else 'Disconnected',C['text']),('Charge state',b.get('status','Unavailable'),C['text']),('Capacity',fmt_capacity(b.get('capacity')),C['text']),('Voltage',fmt_voltage(b.get('voltage')),C['text']),('Battery temperature',fmt_battery_temp(b.get('btemp')),C['text']),('CPU temperature',fmt_thermal(b.get('cpu')),C['green']),('GPU temperature',fmt_thermal(b.get('gpu')),C['green'])]
  if name=='Charging transition':data.append(('Transition',st.result.get('Transition','Waiting'),C['yellow']))
  elif name=='Battery discharge monitor':data.append(('Capacity change',f'{number(b.get("capacity")) - number(st.result.get("Initial capacity")):.0f}%' if number(b.get('capacity')) is not None and number(st.result.get('Initial capacity')) is not None else 'Unavailable',C['yellow']))
  elif name=='Thermal soak monitor':
   cpu=st.result.get('cpu');data.append(('CPU range',f'{cpu[0]/1000:.1f} .. {cpu[1]/1000:.1f} °C' if cpu else 'Unavailable',C['yellow']))
  else:data.append(('Samples tracked',', '.join(k for k in ('capacity','voltage','btemp','cpu','gpu') if k in st.result),C['yellow']))
  return rows(name,'Live monitoring',data,'A: reset  ·  B: back  ·  Menu Hold: category')
 data=[('Status',st.test,status_colour(st.test))]+[(key,value,C['text']) for key,value in st.result.items()]
 if name=='I/O activity':data.append(('Elapsed',f'{elapsed(st):.1f} / 5.0 s',C['cyan']))
 return rows(name,'Storage diagnostic',data,'A: run / restart  ·  B: back  ·  Menu Hold: category')

def change_led(st,delta):
 key=FIELDS[st.field]
 if not st.led.editable(key):st.led.status='Effect controls are read only';return
 st.led.set(key,st.led.value(key)+delta)

def leave_led(st,target):
 st.led.reload();st.page=target
def key(st,b,on):
 if on:
  st.held.add(b);st.seen.add(b)
  if b not in KNOWN:st.unknown.add(b)
  st.last=f'Button {b} {KNOWN.get(b,"UNKNOWN")} DOWN'
  if st.page=='test' and current_test(st)=='Button hold & release':st.press_seen.add(b)
  if st.page=='input':
   if b==MENU_HOLD:st.held.discard(b);st.page='menu';return
   st.down_at[b]=time.monotonic();st.hold_fired.discard(b);return
  if b==MENU_HOLD:
   st.held.discard(b)
   if st.page=='menu':st.running=False
   elif st.page in ('tests','audio','screen','battery','system','storage'):st.page='menu'
   elif st.page=='category':st.page='tests'
   elif st.page=='test':
    stop_worker(st);st.page='category'
   elif st.page=='led':leave_led(st,st.led_return)
   return
  if st.page=='menu':
   if b==0:
    st.page=['input','led','audio','screen','battery','system','storage','tests','exit'][st.sel];st.running=st.page!='exit'
    if st.page=='led':st.led_return='menu'
   elif b==1:st.running=False
  elif st.page=='led':
   if b==0:st.led.save()
   elif b==3:st.led.reload()
   elif b==4:change_led(st,-10)
   elif b==5:change_led(st,10)
   elif b==10:
    field=FIELDS[st.field]
    if field not in ('enabled','effect'):st.led.set(field,0)
   elif b==11:
    field=FIELDS[st.field]
    if field not in ('enabled','effect'):st.led.set(field,255)
   elif b==1:leave_led(st,st.led_return)
  elif st.page=='tests':
   if b==0:st.page='category';st.item=0
   elif b==1:st.page='menu'
  elif st.page=='category':
   if b==0:st.page='test';st.test='Ready'
   elif b==1:st.page='tests'
  elif st.page=='test':
   if b==0:
    if st.cat==1:st.screen_phase=(st.screen_phase+1)%5
    elif current_test(st)=='Stick range & circularity' and st.test=='Running':st.test='Completed'
    else:st.test_armed=True;st.activation_button=0;st.test='Release A to start'
   elif b==1:
    if st.worker and st.worker.poll() is None:stop_worker(st);st.test='Stopped'
    else:st.page='category'
  elif st.page in ('audio','screen','battery','storage'):
   if b==0:
    st.cat={'screen':1,'audio':2,'battery':3,'storage':4}[st.page];st.item=0;st.page='category'
   elif b==1:st.page='menu'
  elif b==1:st.page='menu'
 else:
  st.held.discard(b);st.last=f'Button {b} {KNOWN.get(b,"UNKNOWN")} UP'
  if st.page=='test' and st.test_armed and b==st.activation_button:start_test(st)
  elif st.page=='test' and current_test(st)=='Button hold & release':st.release_seen.add(b)
  st.down_at.pop(b,None);st.hold_fired.discard(b)
def process_input_holds(st):
 if st.page!='input':return False
 now=time.monotonic()
 button=0
 started=st.down_at.get(button)
 if started is not None and button in st.held and button not in st.hold_fired and now-started>=1.5:
  st.hold_fired.add(button);st.led_return='input';st.page='led';st.last='A held 1.5 s'
  return True
 return False

def sdl_error(s):
 value=s.SDL_GetError()
 return value.decode('utf-8','replace') if value else 'unknown SDL error'
def render_texture(s,r):
 rw=s.SDL_RWFromFile(str(FRAME).encode(),b'rb')
 if not rw:raise RuntimeError('SDL_RWFromFile: '+sdl_error(s))
 surface=s.SDL_LoadBMP_RW(rw,1)
 if not surface:raise RuntimeError('SDL_LoadBMP_RW: '+sdl_error(s))
 try:
  texture=s.SDL_CreateTextureFromSurface(r,surface)
  if not texture:raise RuntimeError('SDL_CreateTextureFromSurface: '+sdl_error(s))
  return texture
 finally:s.SDL_FreeSurface(surface)

def main():
 s=load_sdl();bind(s);w=r=j=tx=None;st=None
 print('startup: SDL library loaded',flush=True)
 try:
  if s.SDL_Init(IV|IJ)!=0:raise RuntimeError('SDL_Init: '+sdl_error(s))
  print('startup: SDL initialised',flush=True)
  j=s.SDL_JoystickOpen(0)
  if not j:raise RuntimeError('SDL_JoystickOpen: '+sdl_error(s))
  print('startup: joystick opened',flush=True)
  w=s.SDL_CreateWindow(b'Diagnostics',POS,POS,W,H,FULL)
  if not w:raise RuntimeError('SDL_CreateWindow: '+sdl_error(s))
  print('startup: window created',flush=True)
  r=s.SDL_CreateRenderer(w,-1,ACCEL|VSYNC);renderer_accelerated=bool(r)
  if not r:r=s.SDL_CreateRenderer(w,-1,SOFT)
  if not r:raise RuntimeError('SDL_CreateRenderer: '+sdl_error(s))
  print('startup: renderer created',flush=True)
  st=State(s,j);st.video_driver=(s.SDL_GetCurrentVideoDriver() or b'Unknown').decode(errors='replace');st.renderer_name='accelerated + vsync' if renderer_accelerated else 'software';ev=ctypes.create_string_buffer(64);dirty=True;last=0
  while st.running:
   while s.SDL_PollEvent(ctypes.byref(ev)):
    raw=ev.raw;t=int.from_bytes(raw[:4],sys.byteorder)
    if t==QUIT:st.running=False
    elif t in (DOWN,UP):key(st,raw[12],t==DOWN);dirty=True
    elif t==HAT:
     st.hat=raw[13]
     if st.page=='menu':st.sel=(st.sel-1)%len(MAIN) if st.hat&1 else (st.sel+1)%len(MAIN) if st.hat&4 else st.sel
     elif st.page=='led':
      if st.hat&1:st.field=(st.field-1)%len(FIELDS)
      elif st.hat&4:st.field=(st.field+1)%len(FIELDS)
      elif st.hat&8:change_led(st,-1)
      elif st.hat&2:change_led(st,1)
     elif st.page=='tests':st.cat=(st.cat-1)%len(CATS) if st.hat&1 else (st.cat+1)%len(CATS) if st.hat&4 else st.cat
     elif st.page=='category':
      n=len(CATS[st.cat][1]);st.item=(st.item-1)%n if st.hat&1 else (st.item+1)%n if st.hat&4 else st.item
     elif st.page=='test' and CATS[st.cat][1][st.item]=='D-pad coverage':st.cover.add(st.hat)
     dirty=True
   if st.poll():dirty=True
   if process_input_holds(st):dirty=True
   if update_test(st):dirty=True
   if st.page=='test' and st.cat==1 and current_test(st)=='Motion & tearing':dirty=True
   if dirty and time.monotonic()-last>.06:
    if st.page=='menu':im=listing('Diagnostics',MAIN,st.sel,'D-pad: navigate  ·  A: open  ·  B or Menu Hold: exit')
    elif st.page=='input':im=input_page(st)
    elif st.page=='led':im=led_page(st)
    elif st.page in ('audio','screen','battery','system','storage'):im=overview(st,st.page)
    elif st.page=='tests':im=listing('Tests',[(a,f'{len(b)} tests') for a,b in CATS],st.cat,'D-pad: select  ·  A: open  ·  B or Menu Hold: back')
    elif st.page=='category':im=listing(CATS[st.cat][0],[(x,'') for x in CATS[st.cat][1]],st.item,'D-pad: select  ·  A: open  ·  B or Menu Hold: categories')
    else:im=test_page(st)
    im.save(FRAME,'BMP')
    if tx:s.SDL_DestroyTexture(tx);tx=None
    tx=render_texture(s,r)
    if not tx:raise RuntimeError('SDL texture: '+sdl_error(s))
    if s.SDL_RenderClear(r)!=0:raise RuntimeError('SDL_RenderClear: '+sdl_error(s))
    if s.SDL_RenderCopy(r,tx,None,None)!=0:raise RuntimeError('SDL_RenderCopy: '+sdl_error(s))
    s.SDL_RenderPresent(r);dirty=False;last=time.monotonic()
   s.SDL_Delay(5)
 finally:
  if st is not None:stop_worker(st)
  if st is not None and st.js is not None:
   try:os.close(st.js)
   except OSError:pass
  try:FRAME.unlink()
  except OSError:pass
  if tx:s.SDL_DestroyTexture(tx)
  if r:s.SDL_DestroyRenderer(r)
  if w:s.SDL_DestroyWindow(w)
  if j:s.SDL_JoystickClose(j)
  s.SDL_Quit()
  print('shutdown: SDL resources released',flush=True)

if __name__=='__main__':
 try:main()
 except Exception as exc:
  import traceback
  traceback.print_exc()
  raise SystemExit(1)
