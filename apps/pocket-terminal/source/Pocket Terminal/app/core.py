import codecs, fcntl, os, pty, re, shutil, signal, struct, subprocess, termios, time
from pathlib import Path

ANSI=re.compile(r'\x1b\[([0-9;?]*)([A-Za-z@`~])')
class Cell:
    __slots__=('data','fg','bg','bold','reverse')
    def __init__(self,data=' ',fg='default',bg='black',bold=False,reverse=False): self.data=data;self.fg=fg;self.bg=bg;self.bold=bold;self.reverse=reverse
class Cursor:
    def __init__(self): self.x=0;self.y=0
class Screen:
    def __init__(self,columns,lines):
        self.columns=max(20,columns);self.lines=max(5,lines);self.reset()
    def reset(self):
        self.buffer={};self.primary_buffer=self.buffer;self.alternate_buffer={};self.alternate=False
        self.cursor=Cursor();self.cursor_hidden=False;self.fg='default';self.bg='black';self.bold=False;self.reverse=False
        self.bell_count=0;self.wrap_pending=False;self.autowrap=True;self.application_cursor=False;self.origin_mode=False
        self.scroll_top=0;self.scroll_bottom=self.lines-1;self.saved_state=None
    def resize(self,lines,columns):
        self.lines=max(5,lines);self.columns=max(20,columns);self.buffer=getattr(self,'buffer',{});self.wrap_pending=False
        self.scroll_top=0;self.scroll_bottom=self.lines-1
    def _row(self,y): return self.buffer.setdefault(y,{})
    def _blank(self): return Cell(' ',self.fg,self.bg,self.bold,self.reverse)
    def _cancel_wrap(self): self.wrap_pending=False
    def _scroll_up(self,top=None,bottom=None,count=1):
        top=self.scroll_top if top is None else top;bottom=self.scroll_bottom if bottom is None else bottom
        for _ in range(max(1,count)):
            for y in range(top,bottom): self.buffer[y]=self.buffer.get(y+1,{})
            self.buffer[bottom]={}
    def _scroll_down(self,top=None,bottom=None,count=1):
        top=self.scroll_top if top is None else top;bottom=self.scroll_bottom if bottom is None else bottom
        for _ in range(max(1,count)):
            for y in range(bottom,top,-1): self.buffer[y]=self.buffer.get(y-1,{})
            self.buffer[top]={}
    def _index(self):
        if self.cursor.y==self.scroll_bottom:self._scroll_up()
        else:self.cursor.y=min(self.lines-1,self.cursor.y+1)
    def reverse_index(self):
        self._cancel_wrap()
        if self.cursor.y==self.scroll_top:self._scroll_down()
        else:self.cursor.y=max(0,self.cursor.y-1)
    def next_line(self): self._cancel_wrap();self.cursor.x=0;self._index()
    def _perform_pending_wrap(self):
        if self.wrap_pending:self.wrap_pending=False;self.cursor.x=0;self._index()
    def state(self): return (self.cursor.x,self.cursor.y,self.fg,self.bg,self.bold,self.reverse,self.origin_mode,self.wrap_pending)
    def restore(self,state):
        if state:
            self.cursor.x,self.cursor.y,self.fg,self.bg,self.bold,self.reverse,self.origin_mode,self.wrap_pending=state
            self.cursor.x=min(self.columns-1,self.cursor.x);self.cursor.y=min(self.lines-1,self.cursor.y)
    def use_alternate(self,enabled,save_cursor=True):
        if enabled==self.alternate:return
        if enabled:
            if save_cursor:self.saved_primary_state=self.state()
            self.primary_buffer=self.buffer;self.alternate_buffer={};self.buffer=self.alternate_buffer;self.cursor=Cursor()
        else:
            self.alternate_buffer=self.buffer;self.buffer=self.primary_buffer;self.cursor=Cursor();self.restore(getattr(self,'saved_primary_state',None))
        self.alternate=enabled;self.wrap_pending=False
    def put(self,ch):
        if ch=='\a':self.bell_count+=1;return
        if ch=='\r':self._cancel_wrap();self.cursor.x=0;return
        if ch=='\n':self._cancel_wrap();self._index();return
        if ch=='\b':self._cancel_wrap();self.cursor.x=max(0,self.cursor.x-1);return
        if ch=='\t':self._cancel_wrap();self.cursor.x=min(self.columns-1,(self.cursor.x//8+1)*8);return
        if ord(ch)<32:return
        if self.autowrap:self._perform_pending_wrap()
        self._row(self.cursor.y)[self.cursor.x]=Cell(ch,self.fg,self.bg,self.bold,self.reverse)
        if self.cursor.x==self.columns-1:self.wrap_pending=self.autowrap
        else:self.cursor.x+=1
    def _erase_chars(self,count):
        row=self._row(self.cursor.y)
        for x in range(self.cursor.x,min(self.columns,self.cursor.x+count)):row.pop(x,None)
    def control(self,params,cmd):
        private=params.startswith('?');raw=params.lstrip('?');parsed=[int(v or 0) for v in raw.split(';')] if raw else [0]
        if cmd=='m':
            i=0
            while i<len(parsed):
                v=parsed[i]
                if v==0:self.fg='default';self.bg='black';self.bold=False;self.reverse=False
                elif v==1:self.bold=True
                elif v==22:self.bold=False
                elif v==7:self.reverse=True
                elif v==27:self.reverse=False
                elif 30<=v<=37:self.fg=('black','red','green','brown','blue','magenta','cyan','white')[v-30]
                elif v==39:self.fg='default'
                elif 40<=v<=47:self.bg=('black','red','green','brown','blue','magenta','cyan','white')[v-40]
                elif v==49:self.bg='black'
                elif 90<=v<=97:self.fg=('brightblack','brightred','brightgreen','brightbrown','brightblue','brightmagenta','brightcyan','brightwhite')[v-90]
                elif 100<=v<=107:self.bg=('brightblack','brightred','brightgreen','brightbrown','brightblue','brightmagenta','brightcyan','brightwhite')[v-100]
                i+=1
            return
        self._cancel_wrap();amount=parsed[0] or 1
        if cmd in 'Hf':
            top=self.scroll_top if self.origin_mode else 0;bottom=self.scroll_bottom if self.origin_mode else self.lines-1
            self.cursor.y=max(top,min(bottom,top+(parsed[0] or 1)-1));col=parsed[1] if len(parsed)>1 else 1;self.cursor.x=max(0,min(self.columns-1,(col or 1)-1))
        elif cmd=='A':self.cursor.y=max(self.scroll_top if self.origin_mode else 0,self.cursor.y-amount)
        elif cmd=='B':self.cursor.y=min(self.scroll_bottom if self.origin_mode else self.lines-1,self.cursor.y+amount)
        elif cmd=='C':self.cursor.x=min(self.columns-1,self.cursor.x+amount)
        elif cmd=='D':self.cursor.x=max(0,self.cursor.x-amount)
        elif cmd=='G':self.cursor.x=max(0,min(self.columns-1,amount-1))
        elif cmd=='d':self.cursor.y=max(0,min(self.lines-1,amount-1))
        elif cmd=='J':
            mode=parsed[0]
            if mode in (2,3):self.buffer={};self.primary_buffer=self.buffer if not self.alternate else self.primary_buffer;self.alternate_buffer=self.buffer if self.alternate else self.alternate_buffer
            elif mode==0:
                self.buffer[self.cursor.y]={x:c for x,c in self.buffer.get(self.cursor.y,{}).items() if x<self.cursor.x};self.buffer={y:r for y,r in self.buffer.items() if y<=self.cursor.y}
            elif mode==1:self.buffer[self.cursor.y]={x:c for x,c in self.buffer.get(self.cursor.y,{}).items() if x>self.cursor.x};self.buffer={y:r for y,r in self.buffer.items() if y>=self.cursor.y}
        elif cmd=='K':
            mode=parsed[0];row=self.buffer.get(self.cursor.y,{})
            if mode==0:self.buffer[self.cursor.y]={x:c for x,c in row.items() if x<self.cursor.x}
            elif mode==1:self.buffer[self.cursor.y]={x:c for x,c in row.items() if x>self.cursor.x}
            else:self.buffer[self.cursor.y]={}
        elif cmd=='@':
            row=self._row(self.cursor.y);n=amount
            self.buffer[self.cursor.y]={x+(n if x>=self.cursor.x else 0):c for x,c in row.items() if x+(n if x>=self.cursor.x else 0)<self.columns}
        elif cmd=='P':
            row=self._row(self.cursor.y);n=amount
            self.buffer[self.cursor.y]={x-(n if x>=self.cursor.x+n else 0):c for x,c in row.items() if x<self.cursor.x or x>=self.cursor.x+n}
        elif cmd=='X':self._erase_chars(amount)
        elif cmd=='L':self._scroll_down(self.cursor.y,self.scroll_bottom,amount)
        elif cmd=='M':self._scroll_up(self.cursor.y,self.scroll_bottom,amount)
        elif cmd=='S':self._scroll_up(count=amount)
        elif cmd=='T':self._scroll_down(count=amount)
        elif cmd=='r':
            top=(parsed[0] or 1)-1;bottom=(parsed[1] if len(parsed)>1 and parsed[1] else self.lines)-1
            if 0<=top<bottom<self.lines:self.scroll_top=top;self.scroll_bottom=bottom
            self.cursor.x=0;self.cursor.y=self.scroll_top if self.origin_mode else 0
        elif cmd=='s':self.saved_state=self.state()
        elif cmd=='u':self.restore(self.saved_state)
        elif cmd in ('h','l') and private:
            enabled=cmd=='h'
            for mode in parsed:
                if mode==1:self.application_cursor=enabled
                elif mode==6:self.origin_mode=enabled;self.cursor.x=0;self.cursor.y=self.scroll_top if enabled else 0
                elif mode==7:self.autowrap=enabled;self.wrap_pending=False
                elif mode==25:self.cursor_hidden=not enabled
                elif mode in (47,1047,1049):self.use_alternate(enabled,mode==1049)

class TerminalParser:
    def __init__(self,screen):self.screen=screen;self.pending=''
    def feed(self,text):
        text=self.pending+text;self.pending='';i=0
        while i<len(text):
            if text[i]=='\x1b':
                m=ANSI.match(text,i)
                if m:self.screen.control(m.group(1),m.group(2));i=m.end();continue
                if i==len(text)-1:self.pending='\x1b';break
                code=text[i+1]
                if code=='7':self.screen.saved_state=self.screen.state()
                elif code=='8':self.screen.restore(self.screen.saved_state)
                elif code=='D':self.screen._index()
                elif code=='M':self.screen.reverse_index()
                elif code=='E':self.screen.next_line()
                elif code=='c':self.screen.reset()
                i+=2;continue
            self.screen.put(text[i]);i+=1
class TerminalSession:
    def __init__(self, columns, rows):
        self.screen = Screen(columns, rows)
        self.parser = TerminalParser(self.screen)
        self.process = None
        self.fd = None
        self.kind = None
        self.status = "Stopped"
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    @property
    def connected(self):
        return self.process is not None and self.process.poll() is None

    def start_local(self):
        shell = os.environ.get("SHELL")
        if not shell or not os.path.isfile(shell):
            shell = shutil.which("bash") or shutil.which("sh")
        if not shell:
            raise RuntimeError("Local shell not found")
        arguments = [shell]
        if os.path.basename(shell) == "bash":
            arguments += ["--noprofile", "--norc", "-i"]
        else:
            arguments += ["-i"]
        environment = os.environ.copy()
        environment["TERM"] = "vt100"
        environment.setdefault("PS1", r"\u@\h:\w\$ ")
        self._spawn(arguments, environment)
        self.kind = "local"
        self.status = "Local terminal"

    def _spawn(self, arguments, environment):
        self.close()
        self.screen.reset()
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        master, slave = pty.openpty()
        try:
            self.fd = master
            fcntl.fcntl(master, fcntl.F_SETFL, fcntl.fcntl(master, fcntl.F_GETFL) | os.O_NONBLOCK)
            self._winsize()
            self.process = subprocess.Popen(
                arguments,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=environment,
                cwd=os.environ.get("HOME", "/tmp"),
                close_fds=True,
                start_new_session=True,
            )
        except Exception:
            self._close_fd()
            raise
        finally:
            os.close(slave)

    def resize(self, columns, rows):
        self.screen.resize(rows, columns)
        self.screen.cursor.x = min(self.screen.cursor.x, self.screen.columns - 1)
        self.screen.cursor.y = min(self.screen.cursor.y, self.screen.lines - 1)
        self._winsize()
        if self.connected:
            try:
                os.killpg(self.process.pid, signal.SIGWINCH)
            except OSError:
                pass

    def _winsize(self):
        if self.fd is not None:
            fcntl.ioctl(
                self.fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", self.screen.lines, self.screen.columns, 0, 0),
            )

    def poll(self):
        changed = False
        while self.fd is not None:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            except OSError:
                self._close_fd()
                break
            if not data:
                self._close_fd()
                break
            self.parser.feed(self.decoder.decode(data))
            changed = True
        if self.process is not None:
            result = self.process.poll()
            if result is not None:
                closed = f"Session closed ({result})"
                if self.status != closed:
                    self.status = closed
                    changed = True
        return changed

    def send(self, value):
        if not self.connected or self.fd is None:
            return
        data = value.encode("utf-8") if isinstance(value, str) else value
        try:
            os.write(self.fd, data)
        except OSError:
            pass

    def close(self):
        if self.connected:
            for sig, timeout in ((signal.SIGHUP, 0.5), (signal.SIGTERM, 0.5)):
                try:
                    os.killpg(self.process.pid, sig)
                    self.process.wait(timeout=timeout)
                    break
                except (OSError, subprocess.TimeoutExpired):
                    continue
            if self.process and self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except OSError:
                    pass
        self.process = None
        self._close_fd()
        self.kind = None
        self.status = "Stopped"

    def _close_fd(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

class Repeat:
    def __init__(self):self.value=0;self.next=0
    def update(self,v,now):
        if v!=self.value:self.value=v;self.next=now+.35;return [v] if v else []
        if v and now>=self.next:self.next=now+.08;return [v]
        return []
