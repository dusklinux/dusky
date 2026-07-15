#!/usr/bin/env python3
"""
135_plymouth_setup.py - DUSKY FINAL - Python 3.14.6 - Plymouth 26.134.222-2 ONLY (2026-07-15)
NO BACKWARDS COMPAT - Assumes mkinitcpio 38+, systemd 261+, Plymouth 26+

Critical fixes from forensic audit (6 agents):
- PR #6183: Plymouth 26 reads /etc/vconsole.conf directly, not just keymap.bin. Must FILES+=(/etc/vconsole.conf) or LUKS prompt = US QWERTY = lockout.
- sd-plymouth / plymouth-encrypt REMOVED 2024: use plymouth only.
- Hook order: base systemd plymouth autodetect microcode modconf kms keyboard sd-vconsole sd-encrypt block filesystems fsck (gist + Arch Wiki)
- Parser robustness: anchored ^HOOKS=, last-wins, comment-aware, shlex tokenization. Old [^)]* broke on commented lines, inline comments, .backup false positives.

Pipeline: 070 (masks) -> 120 (creates drop-in) -> 135 THIS -> 158 (mkinitcpio -P)
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
DROPIN = Path("/etc/mkinitcpio.conf.d/10-arch-btrfs-luks.conf")
FILL_B64 = b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="

def run(*cmd, check=True, capture=True):
    return subprocess.run([os.fspath(c) for c in cmd], text=True, capture_output=capture, check=check, timeout=60)

def ensure_root():
    if os.geteuid() != 0:
        console.print("[red]Root required[/red]"); sys.exit(1)

def ensure_plymouth():
    if shutil.which("plymouth-set-default-theme") is None:
        r = run("pacman", "-S", "--needed", "--noconfirm", "plymouth", check=False)
        if r.returncode != 0:
            console.print(Panel("[red]CRITICAL: include 'plymouth' in 070 pacstrap[/red]", box=box.ROUNDED)); sys.exit(1)

def _strip_inline_comment(s: str) -> str:
    """Strip # comment unless inside single/double quotes (bash semantics)"""
    in_s = in_d = False
    esc = False
    for i,ch in enumerate(s):
        if esc:
            esc=False; continue
        if ch == "\\":
            esc=True; continue
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            return s[:i]
    return s

def _parse_last_array(text: str, var: str):
    """
    Find last occurrence of ^\s*VAR=(...) ignoring commented lines.
    Returns (full_match_start, full_match_end, inner_raw, tokens)
    """
    # ^ anchor ensures we ignore # HOOKS= commented lines - matches Arch Wiki grep ^HOOKS practice
    pat = rf'^[ \t]*{re.escape(var)}\s*=\s*\((.*?)\)'
    matches = list(re.finditer(pat, text, flags=re.MULTILINE | re.DOTALL))
    if not matches:
        return None
    last = matches[-1]
    inner = last.group(1)  # inside (...)
    # Clean inline comments line-wise respecting quotes
    cleaned_lines = []
    for line in inner.splitlines():
        cleaned_lines.append(_strip_inline_comment(line))
    cleaned = "\n".join(cleaned_lines)
    # Tokenize with shlex (handles "/etc/foo" quotes)
    try:
        tokens = shlex.split(cleaned)
    except ValueError:
        # fallback: simple split if shlex fails on broken quotes
        tokens = cleaned.split()
    return (last.start(), last.end(), inner, tokens, last)

def _replace_last_array(text: str, var: str, new_tokens: list[str]) -> str:
    """Replace last occurrence of VAR=(...) with new tokens, preserving surrounding file"""
    info = _parse_last_array(text, var)
    if not info:
        # Append at end
        return text.rstrip() + f"\n{var}=({' '.join(new_tokens)})\n"
    start, end, _, _, match_obj = info
    # Build replacement preserving = and parens style
    # Use single line format for simplicity and determinism
    replacement = f"{var}=({' '.join(new_tokens)})"
    # Replace only that occurrence
    return text[:start] + replacement + text[end:]

def deploy_theme():
    console.print(f"[cyan]Deploying theme {THEME_NAME}[/cyan]")
    THEME_DIR.mkdir(parents=True, exist_ok=True)
    (THEME_DIR / "fill.png").write_bytes(base64.b64decode(FILL_B64))
    (THEME_DIR / f"{THEME_NAME}.plymouth").write_text(
        f"""[Plymouth Theme]
Name=Dusky
Description=Dusky elegant synthetic LUKS prompt - Plymouth 26
ModuleName=script

[script]
ImageDir={THEME_DIR}
ScriptFile={THEME_DIR}/{THEME_NAME}.script
ConsoleLogBackgroundColor=0x000000
"""
    )
    (THEME_DIR / f"{THEME_NAME}.script").write_text(
        """Window.SetBackgroundTopColor(0.0, 0.0, 0.0);
Window.SetBackgroundBottomColor(0.0, 0.0, 0.0);
global.password_mode = 0;
screen_w = Window.GetWidth();
screen_h = Window.GetHeight();
if (screen_w == 0) screen_w = 1920;
if (screen_h == 0) screen_h = 1080;
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
    if (progress > global.target_progress) global.target_progress = progress;
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
    console.print(f"[green]Theme {THEME_NAME} set[/green]")

def patch_mkinitcpio():
    console.print(Panel(f"[bold cyan]Patching {DROPIN} for Plymouth 26.134.222-2 (2026-05-30)[/bold cyan]\n- sd-plymouth removed 2024\n- FILES vconsole.conf fix PR #6183", box=box.ROUNDED))

    if not DROPIN.exists():
        DROPIN.parent.mkdir(parents=True, exist_ok=True)
        DROPIN.write_text(
            "MODULES=(btrfs)\n"
            "BINARIES=(/usr/bin/btrfs)\n"
            "HOOKS=(base systemd plymouth keyboard autodetect microcode modconf kms sd-vconsole sd-encrypt block filesystems)\n"
            "FILES=(/etc/vconsole.conf)\n"
        )
        console.print("[green]Created modern drop-in with Plymouth 26 defaults[/green]")
        return

    text = DROPIN.read_text()

    # --- HOOKS handling: absolute best methodology, no backwards compat ---
    # Canonical order for Plymouth 26 + systemd + LUKS + BTRFS (latest gist + Arch Wiki)
    canonical_order = ["base", "systemd", "plymouth", "keyboard", "autodetect", "microcode", "modconf", "kms", "sd-vconsole", "sd-encrypt", "block", "filesystems", "fsck"]
    deprecated = {"sd-plymouth", "plymouth-encrypt", "sd-plymouth-encrypt"}

    hooks_info = _parse_last_array(text, "HOOKS")
    if not hooks_info:
        console.print("[yellow]No HOOKS found, creating modern HOOKS[/yellow]")
        text = text.rstrip() + f"\nHOOKS=({' '.join(canonical_order)})\n"
    else:
        _, _, _, tokens, _ = hooks_info
        # Remove deprecated, dedupe preserving first occurrence
        seen = set()
        cleaned = []
        for t in tokens:
            if t in deprecated:
                console.print(f"[yellow]Removing deprecated hook {t}[/yellow]")
                continue
            if t not in seen:
                cleaned.append(t)
                seen.add(t)
        # Ensure required hooks present
        for req in ["base", "systemd", "plymouth", "keyboard", "sd-vconsole", "sd-encrypt", "block", "filesystems"]:
            if req not in seen:
                cleaned.append(req)
                seen.add(req)

        # Reorder according to canonical_order, unknowns appended at end in original relative order
        ordered = []
        remaining = cleaned.copy()
        for canon in canonical_order:
            if canon in remaining:
                ordered.append(canon)
                remaining.remove(canon)
        # Append leftovers (e.g., custom hooks) preserving order
        ordered.extend(remaining)

        # Final safety: plymouth MUST be before autodetect and before sd-encrypt to show splash before LUKS prompt
        if "plymouth" in ordered and "autodetect" in ordered:
            if ordered.index("plymouth") > ordered.index("autodetect"):
                ordered.remove("plymouth")
                ordered.insert(ordered.index("autodetect"), "plymouth")
                console.print("[cyan]Reordered plymouth before autodetect[/cyan]")

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
        # Exact token match, not substring (fixes /etc/vconsole.conf.backup false positive)
        if target_file not in tokens:
            tokens.append(target_file)
            # Dedupe exact
            tokens = list(dict.fromkeys(tokens))
            text = _replace_last_array(text, "FILES", tokens)
            console.print(f"[green]Patched FILES to include {target_file} (Plymouth 26 LUKS layout fix)[/green]")
        else:
            console.print(f"[dim]FILES already contains {target_file} exactly[/dim]")

    DROPIN.write_text(text)
    DROPIN.chmod(0o644)
    console.print(f"[green]Updated {DROPIN}[/green]")

def main():
    ensure_root()
    console.print(Panel("[bold]135 Plymouth - Dusky FINAL - Plymouth 26 ONLY - 2026-07-15 - No Backwards Compat[/bold]", box=box.ROUNDED))
    ensure_plymouth()
    deploy_theme()
    patch_mkinitcpio()
    console.print(Panel("[bold green]SUCCESS - Plymouth 26 Modern:\n- Hook: plymouth only (sd-plymouth removed 2024)\n- Order: base systemd plymouth keyboard autodetect microcode modconf kms sd-vconsole sd-encrypt block filesystems\n- Fix: FILES=(/etc/vconsole.conf) exact token (PR #6183)\n- Parser: anchored ^HOOKS, last-wins, comment-aware, shlex, idempotent\n- Theme: dusky synthetic[/bold green]", box=box.ROUNDED))

if __name__ == "__main__":
    main()
