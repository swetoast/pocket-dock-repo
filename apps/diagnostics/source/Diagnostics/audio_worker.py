#!/usr/bin/env python3
import ctypes,ctypes.util,math,struct,sys,time
RATE=48000;FMT=0x8010;AUDIO=0x10
class Spec(ctypes.Structure):
 _fields_=[('freq',ctypes.c_int),('format',ctypes.c_uint16),('channels',ctypes.c_uint8),('silence',ctypes.c_uint8),('samples',ctypes.c_uint16),('padding',ctypes.c_uint16),('size',ctypes.c_uint32),('callback',ctypes.c_void_p),('userdata',ctypes.c_void_p)]
def bind(s):
 s.SDL_Init.argtypes=[ctypes.c_uint32];s.SDL_Init.restype=ctypes.c_int;s.SDL_Quit.argtypes=[];s.SDL_Quit.restype=None;s.SDL_GetError.argtypes=[];s.SDL_GetError.restype=ctypes.c_char_p;s.SDL_GetNumAudioDevices.argtypes=[ctypes.c_int];s.SDL_GetNumAudioDevices.restype=ctypes.c_int;s.SDL_GetAudioDeviceName.argtypes=[ctypes.c_int,ctypes.c_int];s.SDL_GetAudioDeviceName.restype=ctypes.c_char_p;s.SDL_OpenAudioDevice.argtypes=[ctypes.c_char_p,ctypes.c_int,ctypes.POINTER(Spec),ctypes.POINTER(Spec),ctypes.c_int];s.SDL_OpenAudioDevice.restype=ctypes.c_uint32;s.SDL_QueueAudio.argtypes=[ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32];s.SDL_QueueAudio.restype=ctypes.c_int;s.SDL_PauseAudioDevice.argtypes=[ctypes.c_uint32,ctypes.c_int];s.SDL_GetQueuedAudioSize.argtypes=[ctypes.c_uint32];s.SDL_GetQueuedAudioSize.restype=ctypes.c_uint32;s.SDL_ClearQueuedAudio.argtypes=[ctypes.c_uint32];s.SDL_CloseAudioDevice.argtypes=[ctypes.c_uint32]
def tone(f,d=.22,a=.045,l=True,r=True):
 out=bytearray()
 for i in range(int(RATE*d)):
  v=int(32767*a*math.sin(2*math.pi*f*i/RATE));out.extend(struct.pack('<hh',v if l else 0,v if r else 0))
 return bytes(out)
def gap(d=.1):return bytes(int(RATE*d)*4)
def payload(mode):
 if mode=='stereo':return tone(660,r=False)+gap()+tone(880,l=False)+gap()+tone(770)
 if mode=='stability':return b''.join(tone(440)+gap()+tone(880)+gap() for _ in range(20))
 return b''.join(tone(f,a=.07)+gap() for f in (60,80,100,125,160,200,250,315,440,660,880,1000))
def main():
 mode=sys.argv[1] if len(sys.argv)>1 else 'stereo';s=ctypes.CDLL(ctypes.util.find_library('SDL2') or 'libSDL2-2.0.so.0');bind(s);dev=0
 try:
  if s.SDL_Init(AUDIO)!=0:return 2
  names=[]
  for i in range(max(0,s.SDL_GetNumAudioDevices(0))):
   raw=s.SDL_GetAudioDeviceName(i,0)
   if raw:names.append(raw.decode(errors='replace'))
  name=next((x for x in names if x.startswith('audiocodec')),None)
  if name is None:return 3
  want=Spec(RATE,FMT,2,0,1024,0,0,None,None);got=Spec();dev=s.SDL_OpenAudioDevice(name.encode(),0,ctypes.byref(want),ctypes.byref(got),15)
  if not dev:return 4
  raw=payload(mode);buf=ctypes.create_string_buffer(raw)
  if s.SDL_QueueAudio(dev,buf,len(raw))!=0:return 5
  s.SDL_PauseAudioDevice(dev,0)
  deadline=time.monotonic()+len(raw)/(RATE*4)+5.0
  while s.SDL_GetQueuedAudioSize(dev):
   if time.monotonic()>deadline:return 6
   time.sleep(.02)
  return 0
 finally:
  if dev:s.SDL_ClearQueuedAudio(dev);s.SDL_CloseAudioDevice(dev)
  s.SDL_Quit()
if __name__=='__main__':raise SystemExit(main())
