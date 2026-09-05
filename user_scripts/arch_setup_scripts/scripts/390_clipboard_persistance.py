#!/usr/bin/env python3
#d: Keep the clipboard persistent across reboots

import os, sys, time, signal, argparse, subprocess, shutil, tempfile
from pathlib import Path
os.umask(0o077)

C_RESET="\033[0m"; C_RED="\033[0;31m"; C_GREEN="\033[0;32m"; C_BLUE="\033[0;34m"; C_YELLOW="\033[1;33m"; C_BOLD="\033[1m"
HOME=Path.home()
STATE_DIR=HOME/".config"/"dusky"/"settings"
STATE_FILE=STATE_DIR/"clipboard_persistance"
DB_ENV_FILE=STATE_DIR/"cliphist_db_env"
QUIET=False
def log_i(m): 
    if not QUIET: print(f"{C_BLUE}[INFO]{C_RESET} {m}")
def log_s(m):
    if not QUIET: print(f"{C_GREEN}[SUCCESS]{C_RESET} {m}")
def log_w(m):
    if not QUIET: print(f"{C_YELLOW}[WARN]{C_RESET} {m}")
def log_e(m): print(f"{C_RED}[ERROR]{C_RESET} {m}", file=sys.stderr)

def write_atomic(p:Path,c:str):
    p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(dir=str(p.parent),text=True)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write(c); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp,0o600); os.replace(tmp,p)
    except:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except: pass
        raise

def get_runtime(): return os.environ.get("XDG_RUNTIME_DIR",f"/run/user/{os.getuid()}")
def env_for(db): 
    e=os.environ.copy(); e["CLIPHIST_DB_PATH"]=db
    if "XDG_RUNTIME_DIR" not in e: e["XDG_RUNTIME_DIR"]=get_runtime()
    return e

def update_config(mode,migrate=False):
    rt=get_runtime(); cache=os.environ.get("XDG_CACHE_HOME",str(HOME/".cache"))
    if mode=="ephemeral":
        db=f"{rt}/cliphist.db"; write_atomic(STATE_FILE,"false\n"); write_atomic(DB_ENV_FILE,f'CLIPHIST_DB_PATH="{db}"\n'); log_s(f"Set to Ephemeral (RAM) -> {db}")
    else:
        td=Path(cache)/"cliphist"; td.mkdir(parents=True,exist_ok=True,mode=0o700)
        db=str(td/"db"); write_atomic(STATE_FILE,"true\n"); write_atomic(DB_ENV_FILE,f'CLIPHIST_DB_PATH="{db}"\n'); log_s(f"Set to Persistent (Disk) -> {db}")
    p=Path(db); p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if p.exists():
        try: os.chmod(p,0o600)
        except: pass
    if migrate:
        other=f"{cache}/cliphist/db" if mode=="ephemeral" else f"{rt}/cliphist.db"
        op=Path(other)
        if op.exists() and op.resolve()!=p.resolve():
            try: shutil.copy2(op,p); log_i(f"Migrated {other} -> {db}")
            except Exception as e: log_w(f"Migration failed: {e}")
    try:
        flag=Path(get_runtime())/"cliphist.skip-store"
        if flag.exists(): flag.unlink(); log_w(f"Removed stale flag {flag}")
    except: pass
    return db

def get_systemd_pids():
    pids = set()
    try:
        r = subprocess.run(["systemctl", "--user", "show", "-p", "MainPID", "--value", "dusky_clipboard.service"], capture_output=True, text=True, timeout=2, check=False)
        val = r.stdout.strip()
        if val.isdigit() and int(val) > 0:
            main_pid = int(val)
            pids.add(main_pid)
            cr = subprocess.run(["pgrep", "-P", str(main_pid)], capture_output=True, text=True, timeout=2, check=False)
            for c in cr.stdout.split():
                if c.isdigit():
                    pids.add(int(c))
    except: pass
    return pids

def _cmdline(pid:int)->str:
    try: raw=Path(f"/proc/{pid}/cmdline").read_bytes()
    except: return ""
    return raw.replace(b"\x00",b" ").decode(errors="replace")

def kill_watchers(exclude_managed=False):
    managed = get_systemd_pids() if exclude_managed else set()
    v=set()
    try:
        r=subprocess.run(["pgrep","-x","wl-paste"],capture_output=True,text=True,check=False)
        for t in r.stdout.split():
            try:
                pid = int(t)
                if pid not in managed: v.add(pid)
            except: pass
    except FileNotFoundError: pass
    try:
        r=subprocess.run(["pgrep","-x","sh"],capture_output=True,text=True,check=False)
        for t in r.stdout.split():
            try: pid=int(t)
            except: continue
            if pid in managed: continue
            c=_cmdline(pid)
            if "wl-paste" in c and "cliphist" in c: v.add(pid)
    except FileNotFoundError: pass
    for pid in sorted(v):
        try: os.kill(pid,signal.SIGTERM)
        except: pass
    dl=time.time()+1.0
    while time.time()<dl and v:
        alive=[p for p in v if Path(f"/proc/{p}").exists()]
        if not alive: break
        time.sleep(0.05); v=set(alive)
    for pid in list(v):
        if Path(f"/proc/{pid}").exists():
            try: os.kill(pid,signal.SIGKILL)
            except: pass
    time.sleep(0.1)

def update_session_env(db):
    try: subprocess.run(["systemctl","--user","set-environment",f"CLIPHIST_DB_PATH={db}"],timeout=5,check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    except FileNotFoundError: log_w("systemctl not found")
    try: subprocess.run(["dbus-update-activation-environment","--systemd","CLIPHIST_DB_PATH"],env=env_for(db),timeout=5,check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    except FileNotFoundError: pass
    hc=shutil.which("hyprctl")
    if not hc: return
    lp=db.replace("\\","\\\\").replace("'","\\'")
    for cmd in ([hc,"setenv","CLIPHIST_DB_PATH",db],[hc,"eval",f"hl.env('CLIPHIST_DB_PATH','{lp}')"]):
        try:
            r=subprocess.run(cmd,timeout=3,check=False,capture_output=True,text=True)
            if r.returncode==0: log_i(f"Hyprland live env updated via: {' '.join(cmd[:2])}"); return
        except: continue

def _bin(): return shutil.which("cliphist")
def list_ids(db):
    b=_bin()
    if not b: return set()
    try: r=subprocess.run([b,"list"],env=env_for(db),capture_output=True,text=True,timeout=3,check=False)
    except: return set()
    s=set()
    for line in r.stdout.splitlines():
        if not line.strip(): continue
        first=line.split("\t")[0].split()[0] if line else ""
        try: s.add(int(first))
        except: continue
    return s

def del_ids(db,ids):
    if not ids: return 0
    b=_bin()
    if not b: return 0
    e=env_for(db); d=0
    for i in sorted(ids):
        try:
            r=subprocess.run([b,"delete"],input=f"{i}\t\n",env=e,capture_output=True,text=True,timeout=3,check=False)
            if r.returncode==0: d+=1
        except: continue
    return d

def reload(db):
    log_i("Live-reloading clipboard daemons...")
    wp=shutil.which("wl-paste"); cb=_bin()
    if not wp or not cb: log_e("wl-paste or cliphist not in PATH"); sys.exit(1)
    
    ids_before=list_ids(db)
    update_session_env(db)
    kill_watchers(exclude_managed=True)
    
    sysd_ok = False
    try:
        # First attempt hot reload (sends SIGHUP to dusky_clipboard_daemon.sh)
        # This instantaneously updates CLIPHIST_DB_PATH for watchers WITHOUT stopping wl-clip-persist,
        # preserving the in-flight clipboard selection across mode switches!
        r = subprocess.run(["systemctl", "--user", "reload", "dusky_clipboard.service"], timeout=3, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            sysd_ok = True
        else:
            r = subprocess.run(["systemctl", "--user", "restart", "dusky_clipboard.service"], timeout=5, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                sysd_ok = True
    except: pass
    
    if not sysd_ok:
        kill_watchers(exclude_managed=False)
        denv = env_for(db)
        cmd_t = ["sh", "-c", 'exec wl-paste --type text --watch sh -c "[ \\\"\\$CLIPBOARD_STATE\\\" = data ] && cliphist store"']
        cmd_i = ["sh", "-c", 'exec wl-paste --type image --watch sh -c "[ \\\"\\$CLIPBOARD_STATE\\\" = data ] && cliphist store"']
        try:
            subprocess.Popen(cmd_t,env=denv,start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.Popen(cmd_i,env=denv,start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except Exception as e: log_e(f"Failed start watchers: {e}"); sys.exit(1)
    
    time.sleep(0.7)
    
    ids_after=list_ids(db)
    leaked=ids_after-ids_before
    missing=ids_before-ids_after
    
    if leaked:
        # If an ID is missing, cliphist deduplicated an existing entry into the new 'leaked' ID.
        # We must keep the leaked ID to prevent destroying the user's data.
        if missing:
            log_i(f"Deduplication detected: cliphist replaced old entry {missing} with {leaked}. Keeping new entry.")
        else:
            n=del_ids(db,leaked)
            if n: log_i(f"Dropped {n} auto-imported entr(y/ies) from OS clipboard (isolation)")
            
    log_s("Daemons reloaded. New mode is active immediately (no reboot needed).")

def menu():
    sys.stdout.write("\033[2J\033[H"); sys.stdout.flush()
    print(f"{C_BOLD}Clipboard Persistence Manager (FIXED v2.2){C_RESET}\nTarget: {DB_ENV_FILE}\n")
    print(f"{C_BOLD}1) Ephemeral (RAM){C_RESET}\n   - $XDG_RUNTIME_DIR/cliphist.db\n   - {C_RED}Lost on reboot{C_RESET}\n")
    print(f"{C_BOLD}2) Persistent (Disk){C_RESET}\n   - $XDG_CACHE_HOME/cliphist/db\n   - {C_GREEN}Survives reboot{C_RESET}\n")
    try: ch=input("Select [1/2] (default 1): ").strip()
    except: print(); sys.exit(130)
    return "ephemeral" if ch in ("","1") else "persistent" if ch=="2" else ""

def main():
    global QUIET
    if os.geteuid()==0: log_e("Do NOT run as root"); sys.exit(1)
    ap=argparse.ArgumentParser(description="Clipboard Persistence Manager (fixed v2.2)")
    g=ap.add_mutually_exclusive_group(); g.add_argument('--ram',action='store_true'); g.add_argument('--disk',action='store_true')
    ap.add_argument('--quiet',action='store_true'); ap.add_argument('--migrate',action='store_true'); a=ap.parse_args(); QUIET=a.quiet
    mode=""
    if a.ram: mode="ephemeral"
    elif a.disk: mode="persistent"
    if not mode:
        if not sys.stdin.isatty(): log_e("Use --ram or --disk for non-tty"); sys.exit(1)
        mode=menu()
        if not mode: log_e("Invalid"); sys.exit(1)
    else: log_i(f"Applying {mode}...")
    db=update_config(mode,migrate=a.migrate); reload(db)

if __name__=="__main__": main()
