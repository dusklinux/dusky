#!/usr/bin/env bash
# Self-contained isolated verification; never alters the live clipboard/history.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 - "$SCRIPT_DIR" "$@" <<'CLIP_VERIFY_PYTHON'
"""Isolated integration tests and benchmarks. Never use the live history/clipboard."""
import argparse, concurrent.futures, fcntl, hashlib, json, os, sys
from pathlib import Path
import pty, select, shutil, signal, socket, statistics, struct, subprocess, tempfile, threading, time, zlib

ROOT = Path(sys.argv.pop(1)).resolve()
SEP = b'\x1f'

def run(cmd, env, data=None, check=True):
    p = subprocess.run(cmd, env=env, input=data, capture_output=True, timeout=20)
    if check and p.returncode:
        raise AssertionError(f'{cmd}: {p.returncode}: {p.stderr.decode(errors="replace")}')
    return p

class Fixture:
    def __init__(self, script):
        self.temp = tempfile.TemporaryDirectory(prefix='clipboard-audit-')
        self.root = Path(self.temp.name)
        self.script = Path(script).resolve()
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith(('CLIP', 'FZF_', 'KITTY_', 'TMUX', 'STY')):
                self.env.pop(key)
        for key, name in [('XDG_CONFIG_HOME','config'), ('XDG_DATA_HOME','data'),
                          ('XDG_CACHE_HOME','cache'), ('XDG_RUNTIME_DIR','runtime')]:
            path = self.root / name
            path.mkdir(mode=0o700)
            self.env[key] = str(path)
        self.env.update(TERM='xterm-256color', CLIPFZF_IMAGE_BACKEND='symbols',
                        CLIPHIST_MAX_ITEMS='6000', CLIPHIST_MAX_DEDUPE_SEARCH='0')
        self.settings = self.root / 'config/dusky/settings'
        self.settings.mkdir(parents=True)
        self.db = self.root / 'runtime/history.db'
        (self.settings / 'cliphist_db_env').write_text(f'CLIPHIST_DB_PATH="{self.db}"\n')
        self.env['CLIPHIST_DB_PATH'] = str(self.db)
        self.state = self.settings / 'clipboard_state'
        self.setstate()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        # Only the clipboard sink and desktop notifications are substituted.
        # cliphist, fzf, image renderers, file, bat, Perl, coreutils are real.
        self.executable('notify-send', '#!/bin/sh\nexit 0\n')
        self.executable('wl-copy', '#!/bin/bash\n[[ ${AUDIT_COPY_FAIL:-0} == 0 ]] || exit 1\nif [[ ${1:-} == --primary ]]; then p=primary; else p=regular; fi\nprintf "%s\\n" "$@" > "$AUDIT_ROOT/$p.args"\ncat > "$AUDIT_ROOT/$p.copy"\n')
        self.executable('fzf', '#!/bin/bash\nexec /usr/bin/fzf --listen="$AUDIT_ROOT/fzf.sock" "$@"\n')
        self.env.update(PATH=str(self.bin)+':'+os.environ['PATH'], AUDIT_ROOT=str(self.root))
        source = self.script.read_text()
        assert source.endswith('main "$@"\n')
        self.lib = self.root / 'library.sh'
        self.lib.write_text(source.removesuffix('main "$@"\n'))
        self.ui('--list')

    def executable(self, name, text):
        p = self.bin / name
        p.write_text(text)
        p.chmod(0o700)

    def setstate(self, vim=False, layout='right,45%,wrap-word'):
        self.state.write_text(f'VIM_MODE="{str(vim).lower()}"\nPREVIEW_LAYOUT="{layout}"\nPREVIEW_LAST="right,45%,wrap-word"\n')

    def ui(self, *args, check=True, extra=None):
        return run([str(self.script), *args], self.env | (extra or {}), check=check)

    def shell(self, code, *args, check=True, extra=None):
        return run(['bash','-c','source "$1"; shift; init_backend_env; setup_dirs; '+code,
                    'audit',str(self.lib), *map(str,args)], self.env | (extra or {}), check=check)

    def store(self, data):
        run(['/usr/bin/cliphist','store'],self.env,data)
        return run(['/usr/bin/cliphist','list'],self.env).stdout.split(b'\t',1)[0].decode()

    def items(self):
        return self.ui('--list').stdout.splitlines()

    def selection(self, rows):
        p = self.root / 'selection'
        p.write_bytes(b'\n'.join(rows)+b'\n')
        return str(p)

    def close(self):
        self.temp.cleanup()

class UI:
    def __init__(self, fixture):
        self.f = fixture
        self.sock = str(fixture.root/'fzf.sock')
        Path(self.sock).unlink(missing_ok=True)
        self.started = time.perf_counter()
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            fcntl.ioctl(0, 0x5414, struct.pack('HHHH',32,120,0,0))
            os.execve(str(fixture.script), [str(fixture.script)],fixture.env)
        os.set_blocking(self.fd,False)
        self.output = b''
        self.reading = True
        self.reader = threading.Thread(target=self.read_loop, daemon=True)
        self.reader.start()
        self.wait(lambda s: s.get('totalCount',0)>0 and s.get('progress')==100)
        self.ready_ms = (time.perf_counter()-self.started)*1000

    def read_loop(self):
        while self.reading:
            if select.select([self.fd],[],[],.01)[0]: self.drain()

    def drain(self):
        try:
            while True:
                b=os.read(self.fd,65536)
                if not b: break
                self.output += b
                # Answer terminal position/DA requests without sending actual keys.
                for _ in range(b.count(b'\x1b[6n')): os.write(self.fd,b'\x1b[1;1R')
                if b'\x1b[?2004$p' in b: os.write(self.fd,b'\x1b[?2004;2$y')
        except (BlockingIOError,OSError): pass

    def http(self, action=None):
        body=(action or '').encode()
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(.3)
            s.connect(self.sock)
            method='POST' if action is not None else 'GET'
            headers = f'Content-Length: {len(body)}\r\n' if action is not None else ''
            s.sendall(f'{method} / HTTP/1.1\r\n{headers}\r\n'.encode()+body)
            data=b''
            while True:
                chunk=s.recv(65536)
                if not chunk: break
                data+=chunk
        payload=data.split(b'\r\n\r\n',1)[1]
        return json.loads(payload) if action is None else payload

    def wait(self, predicate, seconds=5):
        deadline=time.monotonic()+seconds
        last={}
        while time.monotonic()<deadline:
            try:
                last=self.http()
                if predicate(last): return last
            except (OSError,ValueError,IndexError): pass
            time.sleep(.002)
        raise AssertionError(f'UI wait failed: {last}; tail={self.output[-600:]!r}')

    def wait_output(self, marker, seconds=5):
        deadline=time.monotonic()+seconds
        while marker not in self.output and time.monotonic()<deadline:time.sleep(.005)
        assert marker in self.output, self.output[-500:]

    def prompt(self):
        path=self.f.root/'prompt'
        path.unlink(missing_ok=True)
        self.http("execute-silent(printf '%s' \"$FZF_PROMPT\" > \"$AUDIT_ROOT/prompt\")")
        deadline=time.monotonic()+3
        while not path.exists() and time.monotonic()<deadline: time.sleep(.002)
        return path.read_text()

    def key(self, key):
        os.write(self.fd,key)
        time.sleep(.2 if key == b"\x1b" else .06)

    def close(self, abort=True):
        if abort:
            try: self.http('abort')
            except OSError: pass
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            pid,status=os.waitpid(self.pid,os.WNOHANG)
            if pid:
                self.reading=False
                self.reader.join()
                os.close(self.fd)
                return os.waitstatus_to_exitcode(status)
            time.sleep(.005)
        os.kill(self.pid,signal.SIGKILL)
        os.waitpid(self.pid,0)
        self.reading=False
        self.reader.join()
        os.close(self.fd)
        raise AssertionError('UI did not exit')


def png():
    def chunk(t,b): return struct.pack('!I',len(b))+t+b+struct.pack('!I',zlib.crc32(t+b))
    raw=b''.join(b'\0'+b''.join(bytes((x*4,y*4,128)) for x in range(64)) for y in range(48))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',64,48,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')


def suite(script):
    f=Fixture(script)
    passed=[]
    def ok(name):
        passed.append(name)
        print('PASS', name, flush=True)
    try:
        assert b'\x1fempty\x1f' in f.ui('--list').stdout
        ok('empty history sentinel')
        values=[b'alpha no final newline',b'beta\n\n', 'Unicode 界 café 🧑‍💻 नमस्ते'.encode(),
                b'JSON {"x":"quoted"}\tseparated\r\nsecond',b'\x00\x01\xffBINARY',png(),
                b'escape \x1b]52;c;Zm9v\x07 \x1f end', b' ' * 200000 + b'end', b'x'*220000]
        ids=[f.store(v) for v in values]
        rows=f.items()
        assert len(rows)==len(values)
        assert all(len(r.split(SEP))==3 for r in rows)
        for ident,val in zip(ids,values):
            assert run(['cliphist','decode'],f.env,(ident+'\t\n').encode()).stdout==val
        ok('real cliphist byte-for-byte roundtrip: Unicode, NUL, binary, image, escapes, 220KB text')
        rowmap={r.split(SEP)[2].decode():r for r in rows}
        assert rowmap[ids[5]].split(SEP)[1]==b'img'
        assert b'No visual preview' in f.ui('--preview','bin',ids[4]).stdout
        ok('list classification and field/control-character isolation')
        for ident in ids:
            row=rowmap[ident]
            kind=row.split(SEP)[1].decode()
            preview=f.ui('--preview',kind,ident,extra={'FZF_PREVIEW_COLUMNS':'48','FZF_PREVIEW_LINES':'20'}).stdout
            assert preview
        assert b'truncated' in f.ui('--preview','txt',ids[-1]).stdout
        ok('all preview branches and bounded large-text rendering')
        for backend, marker in [('sixels',b'\x1bP'),('kitty',b'\x1b_G'),('iterm',b'\x1b]1337;'),('symbols',b'\x1b['),('none',b'PNG')]:
            out=f.ui('--preview','img',ids[5],extra={'CLIPFZF_IMAGE_BACKEND':backend}).stdout
            assert marker in out and b'No usable' not in out, (backend,out[:100])
        ok('real chafa sixel, kitty, iTerm, symbols and disabled rendering')
        for ident in ids[:5]+[ids[5]]:
            row=rowmap[ident].decode(errors='replace')
            f.shell('cmd_batch_copy "$1"',row)
            expected=values[ids.index(ident)]
            assert (f.root/'regular.copy').read_bytes()==expected
            assert (f.root/'primary.copy').read_bytes()==expected
        ok('single-copy exact bytes and MIME sink for text, binary and image')
        f.shell('cmd_batch_copy "$@"',rowmap[ids[0]].decode(),rowmap[ids[1]].decode())
        assert (f.root/'regular.copy').read_bytes()==values[0]+b'\n'+values[1]
        ok('multi-copy joins only at required newline boundary')
        previous=(f.root/'regular.copy').read_bytes()
        result=f.shell('cmd_batch_copy "$@"',rowmap[ids[0]].decode(),'missing\x1ftxt\x1f999999',check=False)
        assert result.returncode and (f.root/'regular.copy').read_bytes()==previous
        assert f.shell('cmd_batch_copy "$1"',rowmap[ids[0]].decode(),check=False,extra={'AUDIT_COPY_FAIL':'1'}).returncode
        ok('decode/copy failure does not publish partial clipboard')
        select=f.selection([rowmap[ids[0]],rowmap[ids[1]]])
        f.ui('--batch-pin',select)
        pins=[r for r in f.items() if r.split(SEP)[1]==b'pin']
        assert len(pins)==2
        f.ui('--batch-pin',f.selection([pins[0]]))
        assert len([r for r in f.items() if r.split(SEP)[1]==b'pin'])==1
        ok('pin exact payload, order and unpin')
        f.setstate()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: f.ui('--toggle-vim'),range(40)))
        assert b'VIM_MODE="false"' in f.state.read_bytes()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: f.ui('--resize-preview','left'),range(20)))
        assert b'right,90%' in f.state.read_bytes()
        ok('60 concurrent state mutations: no lost toggles, bounded resize')
        f.setstate()
        f.ui('--move-preview','hidden')
        assert b'PREVIEW_LAYOUT="hidden"' in f.state.read_bytes()
        f.ui('--move-preview','hidden')
        assert b'PREVIEW_LAYOUT="right,45%,wrap-word"' in f.state.read_bytes()
        marker=f.root/'injected'
        f.state.write_text(f'VIM_MODE="$(touch {marker})"\nPREVIEW_LAYOUT="garbage"\n')
        f.ui('--vim-init')
        assert not marker.exists()
        ok('hidden pane restores layout; state input never executes shell')
        f.setstate()
        u=UI(f)
        try:
            u.wait(lambda s:s['totalCount']==10)
            u.key(b'alpha')
            u.wait(lambda s:s['query']=='alpha' and s['matchCount']>=1)
            u.key(b'\x1bm')
            assert 'q:quit' in u.prompt()
            u.key(b'/')
            assert 'q:quit' not in u.prompt()
            u.key(b'\x1b')
            assert 'q:quit' in u.prompt()
            u.key(b'\x1bOP')
            time.sleep(.05)
            u.wait_output(b'KEYBINDINGS')
            u.key(b'\x1bOP')
            u.key(b'\x1bv')
            assert b'PREVIEW_LAYOUT="hidden"' in f.state.read_bytes()
            u.key(b'\x1bv')
            u.key(b'\x1bl')
            u.key(b'\x1b[1;3D')
            assert b'right,50%' in f.state.read_bytes()
            u.key(b'\x12')
            u.wait(lambda s:s['totalCount']==10)
        finally:
            assert u.close()==0
        ok('real fzf PTY: typing, Vim toggle/search/Esc, F1, hide/show/move/resize, reload, exit')
        f.setstate(vim=True)
        u=UI(f)
        try:
            assert 'q:quit' in u.prompt()
            u.key(b'j')
            assert u.http()['query']==''
            u.key(b'/alpha')
            u.wait(lambda s:s['query']=='alpha')
        finally: assert u.close()==0
        ok('persisted Vim startup accepts motion and enters search')
        f.setstate()
        u=UI(f)
        try:
            u.http('change-query(alpha)+wait')
            u.wait(lambda s:s['matchCount']>=1 and s['query']=='alpha')
            u.key(b'\r')
            assert u.close(abort=False)==0
            u=None
        finally:
            if u: u.close()
        assert (f.root/'regular.copy').read_bytes()==values[0]
        ok('real fzf Enter selection reaches copy sink unchanged')
        # This runs deletion and confirmed wipe against this fixture only.
        f.ui('--batch-delete',f.selection([rowmap[ids[0]],rowmap[ids[0]],rowmap[ids[4]]]))
        assert ids[0] not in [r.split(SEP)[2].decode() for r in f.items() if r.split(SEP)[1]!=b'pin']
        ok('batch deletion de-duplicates IDs and removes selected histories')
        f.ui('--wipe')
        assert all(r.split(SEP)[1]==b'pin' for r in f.items())
        f.ui('--prune-cache')
        assert not list((f.root/'runtime/cliphist-fzf').glob('*.img'))
        assert not list((f.root/'runtime/cliphist-fzf').glob('session.*'))
        assert not list(f.root.rglob('.clipfzf-*'))
        ok('wipe preserves pins; cache/session/temp cleanup')
        return len(passed)
    finally: f.close()


def benchmark(scripts, count):
    fixtures=[Fixture(s) for s in scripts]
    results={str(s):{'list_ms':[],'open_ready_ms':[],'text_preview_ms':[]} for s in scripts}
    try:
        for f in fixtures:
            # Representative current max-items; no real clipboard data is read.
            for i in range(750): f.store(f'entry {i:04d} synthetic clipboard words 界 café'.encode())
            f.setstate(vim=True)
        for i in range(count+3):
            for f in (fixtures if i%2 else fixtures[::-1]):
                r=results[str(f.script)]
                t=time.perf_counter(); f.ui('--list'); elapsed=(time.perf_counter()-t)*1000
                u=UI(f); ready=u.ready_ms; assert u.close()==0
                t=time.perf_counter(); f.ui('--preview','txt','750',extra={'FZF_PREVIEW_COLUMNS':'55'}); prev=(time.perf_counter()-t)*1000
                if i>=3:
                    r['list_ms'].append(elapsed); r['open_ready_ms'].append(ready); r['text_preview_ms'].append(prev)
        summary={s:{k:{'median':round(statistics.median(v),3),'p95':round(sorted(v)[int(.95*(len(v)-1))],3),'n':len(v)} for k,v in metrics.items()} for s,metrics in results.items()}
        print(json.dumps({'summary':summary,'samples':results},indent=2))
    finally:
        for f in fixtures:f.close()


def stress():
    f=Fixture(ROOT/'terminal_clipboard.sh')
    try:
        for i in range(1200): f.store(f'stress-{i:04d} words 界'.encode())
        rows=f.items(); assert len(rows)==1200
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(lambda i:f.ui('--list') if i%4==0 else f.ui('--preview','txt',str(i%1200+1)),range(240)))
        assert all(p.returncode==0 and p.stdout for p in results)
        print('PASS 1200-entry database: 240 concurrent real list/text preview operations')
        ident=f.store(png())
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(lambda _:f.ui('--preview','img',ident),range(72)))
        assert all(b'No usable' not in p.stdout and b'Failed' not in p.stdout for p in results)
        cached=list((f.root/'runtime/cliphist-fzf').glob('*.img'))
        assert len(cached)==1 and cached[0].read_bytes()==png()
        print('PASS 72 simultaneous image previews: one complete cache payload')
        f.ui('--wipe'); new_id=f.store(b'new generation')
        assert b'new generation' in f.ui('--preview','txt',new_id).stdout
        # The old image ID cannot resolve a cache entry after a wipe.
        assert b'Failed to decode image' in f.ui('--preview','img',ident).stdout
        print('PASS wipe/repopulate invalidates old cached image')
        # A valid PNG with the same ID in another database cannot reuse it either.
        f.ui('--wipe'); image_id=f.store(png()); f.ui('--preview','img',image_id)
        other=f.root/'runtime/other.db'
        run(['cliphist','store'],f.env|{'CLIPHIST_DB_PATH':str(other)},b'other db plain text')
        (f.settings/'cliphist_db_env').write_text(f'CLIPHIST_DB_PATH="{other}"\n')
        assert b'other db plain text' in f.ui('--preview','txt','1').stdout
        assert b'Failed to decode image' in f.ui('--preview','img','1').stdout
        (f.settings/'cliphist_db_env').write_text(f'CLIPHIST_DB_PATH="{f.db}"\n')
        print('PASS switching database path prevents same-ID cache collisions')
        # Invalid database, deleted item, copy failure, malformed input.
        bad=f.root/'runtime/bad.db';bad.write_bytes(b'not a bolt database')
        (f.settings/'cliphist_db_env').write_text(f'CLIPHIST_DB_PATH="{bad}"\n')
        assert b'backend unavailable' in f.ui('--list').stdout
        (f.settings/'cliphist_db_env').write_text(f'CLIPHIST_DB_PATH="{f.db}"\n')
        for typ in ['txt','img','bin','pin']:
            assert f.ui('--preview',typ,'../../escape',check=False).returncode==1
        print('PASS corrupt backend and invalid IDs fail without traversal')
        # Atomic batch delete: one invalid uint64 makes the whole transaction fail.
        image_row=[r for r in f.items() if r.split(SEP)[1]==b'img'][0]
        selection=f.selection([image_row,b'bad\x1ftxt\x1f18446744073709551616'])
        assert f.ui('--batch-delete',selection,check=False).returncode==1
        assert image_row.split(SEP)[2] in [r.split(SEP)[2] for r in f.items()]
        print('PASS failed bulk deletion rolls back real cliphist transaction')
        # Drive actual fzf bindings, including the two-step destructive action.
        f.setstate();u=UI(f)
        try:
            u.key(b'\x1bw')
            assert any(r.split(SEP)[1]==b'img' for r in f.items())
            u.key(b'\x1bw')
            u.wait(lambda s:s.get('current',{}).get('text','').endswith('\x1fempty\x1f'))
        finally: assert u.close()==0
        print('PASS actual Alt-W twice: first press arms, second wipes fixture')
        for i in range(80):f.store(f'delete-me-{i}'.encode())
        f.setstate();u=UI(f)
        try:
            u.http('select-all')
            u.wait(lambda s:len(s['selected'])==80)
            u.key(b'\x1bd')
            u.wait(lambda s:s.get('current',{}).get('text','').endswith('\x1fempty\x1f'))
        finally: assert u.close()==0
        print('PASS actual Alt-D deletes 80 selected records and reloads empty state')
        f.store(b'pin via keyboard');f.setstate();u=UI(f)
        try:
            u.key(b'\x1ba');u.wait(lambda s:s['totalCount']==2)
            u.key(b'\x1bp');u.wait(lambda s:s['matchCount']==1)
            u.key(b'\x1bx');u.wait(lambda s:s['matchCount']==2)
            u.key(b'\x1bi');u.wait(lambda s:s['matchCount']==0)
            u.key(b'\x1bt');u.wait(lambda s:s['matchCount']==1)
            u.key(b'\x1bb');u.wait(lambda s:s['matchCount']==0)
        finally: assert u.close()==0
        print('PASS actual pin binding and text/image/pin/binary/reset filters')
        # Geometry capture and drag persistence use actual sample files, not guesses.
        sess=f.root/f'runtime/cliphist-fzf/session.{os.getpid()}.geometry'
        sess.mkdir()
        env={'CLIPFZF_SESSION':str(sess),'FZF_COLUMNS':'120','FZF_LINES':'32',
             'FZF_PREVIEW_COLUMNS':'50','FZF_PREVIEW_LINES':'28'}
        f.setstate();f.ui('--capture-size',extra=env)
        f.ui('--capture-size',extra=env|{'FZF_PREVIEW_COLUMNS':'62'})
        f.shell('persist_drag_resize',extra=env)
        assert b'right,55%' in f.state.read_bytes()
        shutil.rmtree(sess)
        print('PASS drag geometry delta persists expected 10 percentage-point growth')
        # Test file names and separators in script path using actual fzf callbacks.
        exotic=f.root/"space ' [x] $path; menu.sh"
        shutil.copy2(f.script,exotic);f.script=exotic
        f.setstate();u=UI(f)
        try:
            u.key(b'\x1bm');assert 'q:quit' in u.prompt()
            u.key(b'\x1bOP');u.wait_output(b'KEYBINDINGS')
        finally:assert u.close()==0
        assert not list(f.root.rglob('.clipfzf-*'))
        assert not list((f.root/'runtime/cliphist-fzf').glob('session.*'))
        print('PASS exotic script path, callback quoting, and no leaked sessions/temps')
    finally:f.close()


def daemon():
    f=Fixture(ROOT/'terminal_clipboard.sh')
    p=None
    try:
        child=f.root/'fake-child.py'
        child.write_text('''#!/usr/bin/env python3
import json,os,sys,time
from pathlib import Path
p=Path(os.environ['AUDIT_ROOT'])/('child-'+str(os.getpid()))
p.write_text(json.dumps({'pid':os.getpid(),'args':sys.argv[1:],'db':os.environ.get('CLIPHIST_DB_PATH'),'mode':oct(os.umask(0))}))
while True: time.sleep(1)
''')
        child.chmod(0o700)
        source=(ROOT/'dusky_clipboard_daemon.sh').read_text()
        # Only replace child executables; run the real supervision and env parser.
        source=source.replace('/usr/bin/wl-paste',str(child)).replace('/usr/bin/wl-clip-persist',str(child))
        script=f.root/'daemon.sh';script.write_text(source)
        log=open(f.root/'daemon.log','w+')
        p=subprocess.Popen(['bash',str(script)],env=f.env|{'WAYLAND_DISPLAY':'audit-no-socket'},stdout=log,stderr=log,start_new_session=True)
        def live():
            records=[]
            for file in f.root.glob('child-*'):
                try:
                    obj=json.loads(file.read_text())
                    stat=Path(f'/proc/{obj["pid"]}/stat').read_text().split(') ',1)[1].split()[0]
                    if stat!='Z':records.append(obj)
                except (OSError,ValueError):pass
            return records
        def waitfor(fn):
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                if fn():return
                assert p.poll() is None,(f.root/'daemon.log').read_text()
                time.sleep(.01)
            raise AssertionError(f'daemon wait timeout: {live()}; log={(f.root/"daemon.log").read_text()}')
        waitfor(lambda:len(live())==3)
        assert all(x['mode']=='0o77' and x['db']==str(f.db) for x in live())
        persist=[x['pid'] for x in live() if '--clipboard' in x['args']][0]
        original_pids={x['pid'] for x in live()}
        marker=f.root/'should-not-execute'
        for i in range(12):
            db=f.root/f'runtime/reload-{i}.db'
            (f.settings/'cliphist_db_env').write_text(f'CLIPHIST_DB_PATH="{db}"\ntouch {marker}\n')
            os.kill(p.pid,signal.SIGHUP)
            time.sleep(.01)
            assert {x['pid'] for x in live()}==original_pids
            assert [x['pid'] for x in live() if '--clipboard' in x['args']]==[persist]
        assert not marker.exists()
        print('PASS daemon 12 HUP reloads: all three children retained, no config execution, private umask')
        victim=[x['pid'] for x in live() if '--type' in x['args']][0]
        os.kill(victim,signal.SIGKILL)
        assert p.wait(timeout=5)==1
        time.sleep(.1)
        assert not live()
        log.flush();log.seek(0);out=log.read()
        assert 'status=137' in out and f'pid={victim}' in out
        print('PASS daemon child crash: cleanup, failure exit and identifying journal diagnostic')
        print(out.strip())
    finally:
        if p is not None:
            try:os.killpg(p.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            p.wait(timeout=3)
        f.close()


def persistence():
    """Real switching CLI + real store callbacks; Wayland/systemd are isolated."""
    import shlex
    import types
    from unittest.mock import patch
    script=ROOT.parent/'arch_setup_scripts/scripts/390_clipboard_persistance.py'
    f=Fixture(ROOT/'terminal_clipboard.sh')
    process=None
    try:
        f.env.pop('HYPRLAND_INSTANCE_SIGNATURE',None)
        f.db=f.root/'runtime/cliphist.db'
        f.env['CLIPHIST_DB_PATH']=str(f.db)
        (f.settings/'cliphist_db_env').write_text(f'CLIPHIST_DB_PATH="{f.db}"\n')
        (f.settings/'clipboard_persistance').write_text('false\n')
        disk=f.root/'cache/cliphist/db'
        for kind in ('text','image'):(f.root/('queue-'+kind)).mkdir()
        (f.root/'current-text').write_bytes(b'initial-selection')
        watcher=r"""import os,sys,time,subprocess
from pathlib import Path
root=Path(os.environ['AUDIT_ROOT'])
args=sys.argv[1:];kind=args[args.index('--type')+1];cmd=args[args.index('--watch')+1:]
def store(path):
    result=subprocess.run(cmd,input=path.read_bytes(),env=os.environ|{'CLIPBOARD_STATE':'data'},capture_output=True)
    if result.returncode:
        (root/'callback-errors').write_bytes(result.stderr)
        sys.exit(result.returncode)
initial=root/('current-'+kind)
if initial.exists():store(initial)
(root/('ready-'+kind)).write_text(str(os.getpid()))
seen=set()
while True:
    for item in sorted((root/('queue-'+kind)).glob('*.data')):
        if item.name not in seen:
            store(item);seen.add(item.name);item.with_suffix('.done').touch()
    time.sleep(.002)
"""
        f.executable('wl-paste','#!/bin/bash\nexec -a wl-paste /usr/bin/python3 -c '+shlex.quote(watcher)+' "$@"\n')
        f.executable('wl-clip-persist',"#!/bin/bash\nexec -a wl-clip-persist /usr/bin/python3 -c 'import time; time.sleep(3600)' \"$@\"\n")
        control=r"""#!/usr/bin/python3
import os,sys,json
from pathlib import Path
root=Path(os.environ['AUDIT_ROOT']);args=sys.argv[1:]
if args[:2]==['--user','show']:
    if (root/'health-once').exists():
        (root/'health-once').unlink();print('LoadState=loaded\nActiveState=inactive\nMainPID=0')
    else:print('LoadState=loaded\nActiveState=active\nMainPID='+ (root/'main-pid').read_text())
elif args[:2]==['--user','set-environment']:
    if (root/'fail-once').exists():
        (root/'fail-once').unlink();print('injected environment failure',file=sys.stderr);sys.exit(1)
    if (root/'fail-health-after-set').exists():
        (root/'fail-health-after-set').unlink();(root/'health-once').touch()
    (root/'session-env').write_text(args[2])
else:
    print('Unexpected service action: '+repr(args),file=sys.stderr);sys.exit(9)
"""
        f.executable('systemctl',control)
        f.executable('dbus-update-activation-environment','#!/bin/sh\nexit 0\n')
        source=(ROOT/'dusky_clipboard_daemon.sh').read_text()
        source=source.replace('/usr/bin/wl-paste',str(f.bin/'wl-paste')).replace('/usr/bin/wl-clip-persist',str(f.bin/'wl-clip-persist'))
        source=source.replace('SELF=$(realpath -e -- "${BASH_SOURCE[0]}")','SELF='+shlex.quote(str(ROOT/'dusky_clipboard_daemon.sh')))
        daemon_copy=f.root/'supervisor.sh';daemon_copy.write_text(source)
        log=open(f.root/'supervisor.log','w+')
        process=subprocess.Popen(['bash',str(daemon_copy)],env=f.env|{'WAYLAND_DISPLAY':'isolated'},stdout=log,stderr=log,start_new_session=True)
        (f.root/'main-pid').write_text(str(process.pid))
        def wait_for(fn,seconds=8):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                if fn():return
                assert process.poll() is None,(f.root/'supervisor.log').read_text()
                assert not (f.root/'callback-errors').exists(),(f.root/'callback-errors').read_bytes()
                time.sleep(.005)
            raise AssertionError('persistence test timed out')
        wait_for(lambda:all((f.root/('ready-'+k)).exists() for k in ('text','image')))
        def switch(mode,*extra,check=True):
            return run([str(script),'--'+mode,'--quiet',*extra],f.env,check=check)
        def payloads(db):
            if not db.exists():return []
            env=f.env|{'CLIPHIST_DB_PATH':str(db)}
            ids=[r.split(b'\t',1)[0] for r in run(['cliphist','list'],env).stdout.splitlines()]
            return [run(['cliphist','decode'],env,i+b'\t\n').stdout for i in ids]
        def emit(kind,n,data):
            path=f.root/('queue-'+kind)/f'{n:05d}.data'
            temp=path.with_suffix('.tmp');temp.write_bytes(data);temp.replace(path)
            return path.with_suffix('.done')
        main_before=process.pid
        children_before=Path(f'/proc/{main_before}/task/{main_before}/children').read_text().split()
        assert payloads(f.db)==[b'initial-selection']
        switch('disk')
        assert payloads(disk)==[] and payloads(f.db)==[b'initial-selection']
        done=emit('text',0,b'copied-in-disk');wait_for(done.exists)
        done=emit('image',0,png());wait_for(done.exists)
        assert b'copied-in-disk' in payloads(disk) and png() in payloads(disk)
        switch('ram')
        assert b'copied-in-disk' not in payloads(f.db) and png() not in payloads(f.db)
        done=emit('text',1,b'copied-in-ram');wait_for(done.exists)
        assert b'copied-in-ram' in payloads(f.db) and b'copied-in-ram' not in payloads(disk)
        print('PASS persistence: real text/image callbacks route to RAM/disk; old selection is never re-imported')
        lock=f.settings/'.clipboard_backend.lock'
        with open(lock,'r+b') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            callback=subprocess.Popen([str(ROOT/'dusky_clipboard_daemon.sh'),'--store'],env=f.env|{'CLIPBOARD_STATE':'data'},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            callback.stdin.write(b'queued-copy');callback.stdin.close();callback.stdin=None
            time.sleep(.08);assert callback.poll() is None
            (f.settings/'cliphist_db_env').write_text(f'CLIPHIST_DB_PATH="{disk}"\n')
        _,error=callback.communicate(timeout=5);assert callback.returncode==0,error
        assert b'queued-copy' in payloads(disk) and b'queued-copy' not in payloads(f.db)
        print('PASS store callback waits for switch lock and reads destination after acquiring it')
        state_paths=[f.settings/'cliphist_db_env',f.settings/'clipboard_persistance']
        for failure in ['fail-once','fail-health-after-set']:
            switch('ram');before=[p.read_bytes() for p in state_paths]
            (f.root/failure).touch();result=switch('disk',check=False)
            assert result.returncode==1 and b'previous configuration restored' in result.stderr
            assert [p.read_bytes() for p in state_paths]==before
            assert (f.root/'session-env').read_text()=='CLIPHIST_DB_PATH='+str(f.db)
        print('PASS failed environment update and service-health change roll back both settings and session path')
        original_ram=set(payloads(f.db));original_disk=set(payloads(disk))
        expected={f'concurrent-copy-{i:03d}'.encode() for i in range(100)}
        def produce():
            completions=[]
            for i,data in enumerate(sorted(expected)):
                completions.append(emit('text',i+10,data));time.sleep(.002)
            for done in completions:wait_for(done.exists,seconds=20)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            jobs=[pool.submit(produce)]
            jobs += [pool.submit(switch,'ram' if i%2 else 'disk') for i in range(80)]
            for job in jobs:job.result(timeout=30)
        ram=set(payloads(f.db));disc=set(payloads(disk))
        assert expected <= ram|disc and not(expected & ram & disc)
        assert original_ram <= ram and original_disk <= disc
        assert Path(f'/proc/{main_before}/task/{main_before}/children').read_text().split()==children_before
        print('PASS 80 concurrent switches + 100 new copies: no lost copies, cross-database duplicates, or watcher restarts')
        for state in ('sensitive','clear','nil',''):
            run([str(ROOT/'dusky_clipboard_daemon.sh'),'--store'],f.env|{'CLIPBOARD_STATE':state},b'must-not-store')
        assert set(payloads(f.db))==ram and set(payloads(disk))==disc
        print('PASS sensitive/clear/non-data events neither store nor delete history')
        switch('ram');before=[p.read_bytes() for p in state_paths]
        result=switch('disk','--migrate',check=False)
        assert result.returncode==1 and b'destination already exists' in result.stderr
        assert [p.read_bytes() for p in state_paths]==before and set(payloads(disk))==disc
        old_disk=f.root/'old-disk.db';disk.rename(old_disk)
        switch('disk','--migrate')
        assert set(payloads(disk))==ram and set(payloads(old_disk))==disc
        assert stat_mode(disk)==0o600
        print('PASS migration preserves original database; existing destination refused; new destination verified byte-for-byte')
        switch('ram');before=[p.read_bytes() for p in state_paths]
        disk.rename(f.root/'migrated-disk.db');disk.write_bytes(b'corrupt database')
        assert switch('disk',check=False).returncode==1
        assert [p.read_bytes() for p in state_paths]==before
        print('PASS corrupt destination rejected before changing active backend')
        # Direct unit failure injection for lock timeout and a stopped service.
        module=types.ModuleType('persistence_under_test');module.__file__=str(script)
        with patch.dict(os.environ,f.env):exec(compile(script.read_text(),str(script),'exec'),module.__dict__)
        with open(lock,'r+b') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            try:
                with module.locked(lock,timeout=.05):raise AssertionError('lock unexpectedly acquired')
            except module.SwitchError:pass
        with patch.object(module,'service_state',return_value=None),patch.object(module,'update_launch_environments'):
            with patch.dict(os.environ,f.env):module.switch('ram')
        print('PASS bounded lock timeout and configuration-only switch while service is stopped')
        # Interrupt between the two settings-file publications: rollback must
        # restore both files even though the first rename already succeeded.
        before=[p.read_bytes() for p in state_paths]
        original_write=module.write_atomic
        calls=0
        def interrupted_write(path,data):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('injected disk write failure')
            original_write(path,data)
        disk.unlink()
        with patch.dict(os.environ,f.env),patch.object(module,'write_atomic',side_effect=interrupted_write):
            try:module.switch('disk');raise AssertionError('write failure ignored')
            except module.SwitchError as exc:assert 'previous configuration restored' in str(exc)
        assert [p.read_bytes() for p in state_paths]==before
        print('PASS write failure between settings publications restores previous configuration')
        # A writer holding Bolt's real flock cannot race a migration snapshot.
        snapshot=f.root/'cache/locked-snapshot.db'
        with open(f.db,'r+b') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(module.migrate_snapshot,f.db,snapshot)
                time.sleep(.08);assert not future.done()
                fcntl.flock(handle,fcntl.LOCK_UN)
                future.result(timeout=6)
        assert set(payloads(snapshot))==ram
        print('PASS migration waits for real Bolt file lock and publishes a complete snapshot')

        assert process.poll() is None
        assert not list(f.settings.glob('.clipboard-*'))
    finally:
        if process is not None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            process.wait(timeout=5)
        f.close()


def stat_mode(path):
    return path.stat().st_mode & 0o777


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script',type=Path,default=ROOT/'terminal_clipboard.sh')
    parser.add_argument('--benchmark',type=Path,nargs='+')
    parser.add_argument('--runs',type=int,default=30)
    parser.add_argument('--persistence-only',action='store_true')
    args=parser.parse_args()
    if args.persistence_only:
        persistence()
        sys.exit(0)
    if args.benchmark: benchmark([p.resolve() for p in args.benchmark],args.runs)
    else: print(f'{suite(args.script)} groups passed')

    if len(sys.argv) == 1:
        stress()
        daemon()
        persistence()
CLIP_VERIFY_PYTHON
