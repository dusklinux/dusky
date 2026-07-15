#!/usr/bin/env python3
"""
135_plymouth_setup.py - DUSKY - Python 3.14.6 + Rich 15.0.0
Modern Plymouth 26.134.222-2 support (Arch 2026-05-30)
Fixes:
  - Plymouth 26 reads keyboard layout from /etc/vconsole.conf, not just keymap.bin.
    Omarchy PR #6183: add FILES+=(/etc/vconsole.conf) or LUKS prompt falls back to US QWERTY.
  - sd-plymouth / plymouth-encrypt hooks removed Jan 2024: WARNING The plymouth-encrypt and sd-plymouth hooks no longer exist, replace with encrypt and plymouth.
  - Recommended order for systemd initramfs (Arch Wiki + gist):
    HOOKS=(base systemd plymouth autodetect microcode modconf kms keyboard sd-vconsole sd-encrypt block filesystems fsck)
  - Robust parser: anchored ^HOOKS, last-wins, comment-aware, shlex exact token (fixes commented HOOKS, inline #, .backup false positive)

Pipeline: Runs AFTER 120_mkinitcpio_optimizer (creates drop-in) and BEFORE 158_mkinitcpio_restore_and_generate
"""
from __future__ import annotations
import os, sys, re, shlex, base64, shutil, subprocess
from pathlib import Path

def _ensure_rich():
    import importlib.util
    if importlib.util.find_spec("rich") is None:
        subprocess.run(["pacman", "-Sy", "--needed", "--noconfirm", "python-rich"], check=False)
_ensure_rich()
from rich.console import Console
from rich.panel import Panel
from rich import box

def make_console():
    term = os.environ.get("TERM", "")
    if term in ("dumb", "unknown"):
        return Console(color_system=None, force_terminal=False, no_color=True, legacy_windows=False)
    return Console(color_system="standard", legacy_windows=False, safe_box=True, highlight=False, markup=True)

console = make_console()

THEME_NAME = "dusky"
THEME_DIR = Path(f"/usr/share/plymouth/themes/{THEME_NAME}")
MKINITCPIO_DROPIN = Path("/etc/mkinitcpio.conf.d/10-arch-btrfs-luks.conf")

FILL_PNG_B64 = b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="

def run(*cmd, check=True, capture=True):
    return subprocess.run([os.fspath(c) for c in cmd], text=True, capture_output=capture, check=check, timeout=60)

def ensure_root():
    if os.geteuid() != 0:
        console.print("[red][FATAL] Root required[/red]")
        sys.exit(1)

def ensure_plymouth():
    if shutil.which("plymouth-set-default-theme") is None:
        console.print("[yellow]Plymouth not installed, installing offline...[/yellow]")
        r = run("pacman", "-S", "--needed", "--noconfirm", "plymouth", check=False)
        if r.returncode != 0:
            console.print(Panel("[red]CRITICAL: plymouth install failed. Include 'plymouth' in 070_pacstrap payload.[/red]", box=box.ROUNDED))
            sys.exit(1)
    console.print("[green]Plymouth binaries present[/green]")

def _strip_inline_comment(s: str) -> str:
    """Strip # comment unless inside single/double quotes (bash semantics)"""
    in_s = in_d = False
    esc = False
    for i, ch in enumerate(s):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            return s[:i]
    return s

def _parse_last_array(text: str, var: str):
    r"""
    Find last occurrence of ^\s*VAR=(...) ignoring commented lines.
    Arch Wiki grep ^HOOKS practice - anchored to prevent # HOOKS= poisoning.
    Returns (start, end, inner, tokens, match) or None.
    """
    pat = rf'^[ \t]*{re.escape(var)}\s*=\s*\((.*?)\)'
    matches = list(re.finditer(pat, text, flags=re.MULTILINE | re.DOTALL))
    if not matches:
        return None
    last = matches[-1]
    inner = last.group(1)
    cleaned_lines = []
    for line in inner.splitlines():
        cleaned_lines.append(_strip_inline_comment(line))
    cleaned = "\n".join(cleaned_lines)
    try:
        tokens = shlex.split(cleaned)
    except ValueError:
        tokens = cleaned.split()
    return (last.start(), last.end(), inner, tokens, last)

def _replace_last_array(text: str, var: str, new_tokens: list[str]) -> str:
    """Replace last occurrence of VAR=(...) with new tokens, or append if missing"""
    info = _parse_last_array(text, var)
    if not info:
        return text.rstrip() + f"\n{var}=({' '.join(new_tokens)})\n"
    start, end, _, _, _ = info
    replacement = f"{var}=({' '.join(new_tokens)})"
    return text[:start] + replacement + text[end:]

def deploy_theme():
    console.print(f"[cyan]Deploying self-contained theme: {THEME_NAME} -> {THEME_DIR}[/cyan]")
    THEME_DIR.mkdir(parents=True, exist_ok=True)

    fill_path = THEME_DIR / "fill.png"
    fill_path.write_bytes(base64.b64decode(FILL_PNG_B64))

    (THEME_DIR / f"{THEME_NAME}.plymouth").write_text(
        f"""[Plymouth Theme]
Name=Dusky
Description=Dusky elegant synthetic graphical LUKS prompt and splash.
ModuleName=script

[script]
ImageDir={THEME_DIR}
ScriptFile={THEME_DIR}/{THEME_NAME}.script
ConsoleLogBackgroundColor=0x000000
"""
    )

    (THEME_DIR / f"{THEME_NAME}.script").write_text(
        """// --- Window Background (Pitch Black) ---
Window.SetBackgroundTopColor(0.0, 0.0, 0.0);
Window.SetBackgroundBottomColor(0.0, 0.0, 0.0);

global.password_mode = 0;
screen_w = Window.GetWidth();
screen_h = Window.GetHeight();

if (screen_w == 0) screen_w = 1920;
if (screen_h == 0) screen_h = 1080;

// --- DUSKY Text Logo ---
logo.image = Image.Text("dusky", 0.9, 0.9, 0.9, 1.0, "Sans Light 32");
logo.sprite = Sprite(logo.image);
logo.x = screen_w / 2 - logo.image.GetWidth() / 2;
logo.y = screen_h / 2 - logo.image.GetHeight() / 2 - 40;
logo.sprite.SetPosition(logo.x, logo.y, 10);

global.bar_width = 150;
global.bar_height = 4;

track.image = Image("fill.png").Scale(global.bar_width, global.bar_height);
track.sprite = Sprite(track.image);
track.x = screen_w / 2 - global.bar_width / 2;
track.y = logo.y + logo.image.GetHeight() + 50; 
track.sprite.SetPosition(track.x, track.y, 10);
track.sprite.SetOpacity(0.2);

fill.original_image = Image("fill.png");
fill.sprite = Sprite();
fill.sprite.SetPosition(track.x, track.y, 11);
fill.sprite.SetOpacity(0.9);

global.current_progress = 0.0;
global.target_progress = 0.0;
global.last_fill_w = 0;

fun refresh_callback () {
    if (global.current_progress < global.target_progress) {
        global.current_progress += 0.005;
        global.current_progress += (global.target_progress - global.current_progress) * 0.1;
    }
    if (global.current_progress > 1.0) global.current_progress = 1.0;

    if (global.password_mode == 0) {
        fill_w = Math.Int(global.bar_width * global.current_progress);
        if (fill_w < 1) fill_w = 1;
        if (global.last_fill_w != fill_w) {
            fill_img = fill.original_image.Scale(fill_w, global.bar_height);
            fill.sprite.SetImage(fill_img);
            global.last_fill_w = fill_w;
        }
        fill.sprite.SetPosition(track.x, track.y, 11);
    }
}
Plymouth.SetRefreshFunction(refresh_callback);

fun progress_callback(duration, progress) {
    if (progress > global.target_progress) {
        global.target_progress = progress;
    }
}
Plymouth.SetBootProgressFunction(progress_callback);

status_sprite = Sprite();
fun status_callback(status) {
    if (global.password_mode == 0) {
        status_img = Image.Text(status, 0.4, 0.4, 0.4, 1.0, "Monospace 10"); 
        status_sprite.SetImage(status_img);
        status_sprite.SetX(screen_w / 2 - status_img.GetWidth() / 2);
        status_sprite.SetY(screen_h * 0.90);
        status_sprite.SetOpacity(1);
    }
}
Plymouth.SetUpdateStatusFunction(status_callback);

prompt_sprite = Sprite();
bullets_sprite = Sprite();

fun display_password_callback(prompt_ignored, bullets) {
    global.password_mode = 1;
    fill.sprite.SetOpacity(0);
    track.sprite.SetOpacity(0);
    status_sprite.SetOpacity(0);
    prompt_img = Image.Text("unlock", 0.7, 0.7, 0.7, 1.0, "Sans Light 16");
    prompt_sprite.SetImage(prompt_img);
    prompt_sprite.SetX(screen_w / 2 - prompt_img.GetWidth() / 2);
    prompt_sprite.SetY(logo.y + logo.image.GetHeight() + 40);
    prompt_sprite.SetOpacity(1);
    bullets_str = "";
    for (i = 0; i < bullets; i++) bullets_str += "*";
    if (bullets == 0) bullets_str = " "; 
    bullets_img = Image.Text(bullets_str, 1.0, 1.0, 1.0, 1.0, "Monospace 16");
    bullets_sprite.SetImage(bullets_img);
    bullets_sprite.SetX(screen_w / 2 - bullets_img.GetWidth() / 2);
    bullets_sprite.SetY(prompt_sprite.GetY() + 25);
    bullets_sprite.SetOpacity(1);
}

fun display_normal_callback() {
    global.password_mode = 0;
    prompt_sprite.SetOpacity(0);
    bullets_sprite.SetOpacity(0);
    track.sprite.SetOpacity(0.2);
    fill.sprite.SetOpacity(0.9);
    status_sprite.SetOpacity(1);
}

Plymouth.SetDisplayPasswordFunction(display_password_callback);
Plymouth.SetDisplayNormalFunction(display_normal_callback);
"""
    )

    for p in THEME_DIR.iterdir():
        p.chmod(0o644)
    THEME_DIR.chmod(0o755)

    run("plymouth-set-default-theme", THEME_NAME, capture=False)
    console.print(f"[green]Theme {THEME_NAME} set as default[/green]")

def patch_mkinitcpio():
    console.print(Panel(f"[bold cyan]Patching mkinitcpio drop-in {MKINITCPIO_DROPIN} for Plymouth 26.134.222-2 (2026-05-30)[/bold cyan]", box=box.ROUNDED))

    if not MKINITCPIO_DROPIN.exists():
        console.print(f"[yellow]WARN: {MKINITCPIO_DROPIN} not found, run 120 before 135[/yellow]")
        MKINITCPIO_DROPIN.parent.mkdir(parents=True, exist_ok=True)
        MKINITCPIO_DROPIN.write_text(
            'MODULES=(btrfs)\nBINARIES=(/usr/bin/btrfs)\nHOOKS=(base systemd plymouth keyboard autodetect microcode modconf kms sd-vconsole sd-encrypt block filesystems)\nFILES=(/etc/vconsole.conf)\n'
        )
        console.print("[green]Created minimal drop-in with plymouth + vconsole.conf[/green]")
        return

    text = MKINITCPIO_DROPIN.read_text()

    # Canonical order for Plymouth 26 + systemd + LUKS + BTRFS (latest gist + Arch Wiki)
    canonical_order = ["base", "systemd", "plymouth", "keyboard", "autodetect", "microcode", "modconf", "kms", "sd-vconsole", "sd-encrypt", "block", "filesystems", "fsck"]
    deprecated = {"sd-plymouth", "plymouth-encrypt", "sd-plymouth-encrypt"}

    hooks_info = _parse_last_array(text, "HOOKS")
    if not hooks_info:
        console.print("[yellow]No HOOKS found, creating modern HOOKS[/yellow]")
        text = text.rstrip() + f"\nHOOKS=({' '.join(canonical_order)})\n"
    else:
        _, _, _, tokens, _ = hooks_info
        seen = set()
        cleaned = []
        for t in tokens:
            if t in deprecated:
                console.print(f"[yellow]Removing deprecated hook {t}[/yellow]")
                continue
            if t not in seen:
                cleaned.append(t)
                seen.add(t)
        for req in ["base", "systemd", "plymouth", "keyboard", "sd-vconsole", "sd-encrypt", "block", "filesystems"]:
            if req not in seen:
                cleaned.append(req)
                seen.add(req)

        ordered = []
        remaining = cleaned.copy()
        for canon in canonical_order:
            if canon in remaining:
                ordered.append(canon)
                remaining.remove(canon)
        ordered.extend(remaining)

        if "plymouth" in ordered and "autodetect" in ordered:
            if ordered.index("plymouth") > ordered.index("autodetect"):
                ordered.remove("plymouth")
                ordered.insert(ordered.index("autodetect"), "plymouth")
                console.print("[cyan]Reordered plymouth before autodetect (modern)[/cyan]")

        text = _replace_last_array(text, "HOOKS", ordered)
        console.print(f"[green]HOOKS fixed: {' '.join(ordered)}[/green]")

    # --- FILES handling: exact token match, not substring, for PR #6183 fix ---
    files_info = _parse_last_array(text, "FILES")
    target_file = "/etc/vconsole.conf"

    if not files_info:
        text = text.rstrip() + f"\nFILES=({target_file})\n"
        console.print(f"[green]Added FILES=({target_file})[/green]")
    else:
        _, _, _, tokens, _ = files_info
        if target_file not in tokens:  # exact token, not substring - fixes /etc/vconsole.conf.backup false positive
            tokens.append(target_file)
            tokens = list(dict.fromkeys(tokens))
            text = _replace_last_array(text, "FILES", tokens)
            console.print(f"[green]Patched FILES to include {target_file} (Plymouth 26 LUKS layout fix PR #6183)[/green]")
        else:
            console.print(f"[dim]FILES already contains {target_file} exactly[/dim]")

    MKINITCPIO_DROPIN.write_text(text)
    MKINITCPIO_DROPIN.chmod(0o644)
    console.print(f"[green]Updated {MKINITCPIO_DROPIN}[/green]")

def main():
    ensure_root()
    console.print(Panel("[bold]135 Plymouth Setup - Dusky - Plymouth 26 Modern (2026-07-15) - Robust Parser[/bold]", box=box.ROUNDED))
    ensure_plymouth()
    deploy_theme()
    patch_mkinitcpio()
    console.print(Panel("[bold green]Plymouth deployment successful.\n- Hook: plymouth only (sd-plymouth removed Jan 2024)\n- Order: base systemd plymouth keyboard autodetect microcode modconf kms sd-vconsole sd-encrypt block filesystems\n- Fix: FILES=(/etc/vconsole.conf) exact token (PR #6183)\n- Parser: anchored ^HOOKS, last-wins, comment-aware, shlex, idempotent, fixes # HOOKS= poisoning and inline # brick\n- Deferred: initramfs generation to 158[/bold green]", box=box.ROUNDED))

if __name__ == "__main__":
    main()
