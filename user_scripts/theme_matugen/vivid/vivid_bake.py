#!/usr/bin/env python3
"""
===============================================================================
DUSKY VIVID THEME PACK BAKER (v2.1)
===============================================================================
Target: Arch Linux / Hyprland / Matugen / Python 3.15
Compiles immutable, pre-baked desktop theme packs into ~/.config/matugen/prebaked.
Zero Git pollution. Deterministic input fingerprints, Manifest v2 with SHA-256,
staged private template rendering, strict output inventory enforcement,
in-memory payload delivery, and single fixed cache lock released between presets.
===============================================================================
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import colorsys
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from typing import Any
import xml.etree.ElementTree as ET

import vivid_data
from vivid_data import (
    CACHE_LOCK_PATH,
    CACHE_ROOT,
    CATALOG_PATH,
    LIVE_CONFIG,
    PACKS_ROOT,
    PRESETS_DIR,
    STAGING_ROOT,
    PresetDict,
)

COMPILER_VERSION = "v2.1"


@dataclass(frozen=True)
class PreparedPack:
    id: str
    label: str
    hex: str
    bg_hex: str
    mode: str
    scheme: str
    order: int
    desc: str
    fingerprint: str
    source_render_digest: str
    pack_dir: Path
    payloads: dict[str, bytes]
    message: str


@contextmanager
def cache_lock():
    """
    Acquires an exclusive flock on CACHE_LOCK_PATH.
    Opens existing lock file read-only (supporting LOCK_EX on Linux),
    or creates it if absent and directory is writable.
    """
    fd = -1
    try:
        if CACHE_LOCK_PATH.is_file():
            try:
                fd = os.open(CACHE_LOCK_PATH, os.O_RDONLY)
            except OSError:
                pass
        if fd < 0:
            CACHE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(CACHE_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)

        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fd >= 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def hex_to_rgb(hex_str: str) -> tuple[float, float, float]:
    h = hex_str.strip().lstrip("#")
    return int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0


def rgb_to_hex(r: float, g: float, b: float) -> str:
    return f"#{int(round(r * 255)):02x}{int(round(g * 255)):02x}{int(round(b * 255)):02x}"


def relative_luminance(r: float, g: float, b: float) -> float:
    def adjust(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * adjust(r) + 0.7152 * adjust(g) + 0.0722 * adjust(b)


def derive_surface_ramp(bg_hex: str, mode: str = "dark") -> dict[str, str]:
    """Generates a progressive surface ramp from the chosen background hex."""
    r, g, b = hex_to_rgb(bg_hex)
    h, l, s = colorsys.rgb_to_hls(r, g, b)

    ramp: dict[str, str] = {}
    ramp["surface_container_lowest"] = bg_hex

    if mode == "light":
        steps = [
            ("surface_container_low", max(0.88, l - 0.03)),
            ("surface", max(0.84, l - 0.05)),
            ("surface_container", max(0.80, l - 0.08)),
            ("surface_container_high", max(0.75, l - 0.12)),
            ("surface_container_highest", max(0.70, l - 0.16)),
            ("surface_bright", min(1.0, l)),
            ("surface_dim", max(0.75, l - 0.12)),
        ]
    else:
        steps = [
            ("surface_container_low", min(0.35, l + 0.02)),
            ("surface", min(0.40, l + 0.03)),
            ("surface_container", min(0.45, l + 0.05)),
            ("surface_container_high", min(0.50, l + 0.07)),
            ("surface_container_highest", min(0.55, l + 0.09)),
            ("surface_bright", min(0.60, l + 0.12)),
            ("surface_dim", max(0.02, l - 0.02)),
        ]

    for name, new_l in steps:
        nr, ng, nb = colorsys.hls_to_rgb(h, new_l, s)
        ramp[name] = rgb_to_hex(nr, ng, nb)

    return ramp


def generate_preset_palette(
    preset: PresetDict,
    matugen_path: str = "/usr/bin/matugen",
    config_path: Path | str | None = None,
) -> dict:
    hex_code = preset["hex"].strip().upper()
    bg_hex = preset["bg_hex"].strip().lower()
    mode = preset.get("mode", "dark")
    scheme = preset.get("scheme", "scheme-fidelity" if hex_code not in ("#FFFFFF", "#000000") else "scheme-monochrome")

    # Matugen seed color
    matugen_seed = hex_code if hex_code not in ("#000000", "#FFFFFF") else ("#000000" if mode == "light" else "#FFFFFF")
    cmd = [matugen_path]
    if config_path:
        cmd.extend(["--config", str(config_path)])
    cmd.extend([
        "--dry-run",
        "--json",
        "hex",
        "--mode",
        mode,
        "-t",
        scheme,
        "color",
        "hex",
        matugen_seed,
    ])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
    data = json.loads(proc.stdout)

    ramp = derive_surface_ramp(bg_hex, mode)

    r, g, b = hex_to_rgb(hex_code)
    lum = relative_luminance(r, g, b)
    on_primary_hex = "#000000" if lum > 0.4 else "#ffffff"

    h_bg, _, s_bg = colorsys.rgb_to_hls(*hex_to_rgb(bg_hex))
    if mode == "light":
        tr, tg, tb = colorsys.hls_to_rgb(h_bg, 0.10, min(0.2, s_bg))
        on_surface_hex = rgb_to_hex(tr, tg, tb)

        tr_var, tg_var, tb_var = colorsys.hls_to_rgb(h_bg, 0.28, min(0.2, s_bg))
        on_surface_variant_hex = rgb_to_hex(tr_var, tg_var, tb_var)

        or_, og, ob = colorsys.hls_to_rgb(h_bg, 0.48, min(0.3, s_bg + 0.1))
        outline_hex = rgb_to_hex(or_, og, ob)

        ovr, ovg, ovb = colorsys.hls_to_rgb(h_bg, 0.78, min(0.3, s_bg))
        outline_variant_hex = rgb_to_hex(ovr, ovg, ovb)
    else:
        tr, tg, tb = colorsys.hls_to_rgb(h_bg, 0.92, min(0.4, s_bg))
        on_surface_hex = rgb_to_hex(tr, tg, tb)

        tr_var, tg_var, tb_var = colorsys.hls_to_rgb(h_bg, 0.80, min(0.3, s_bg))
        on_surface_variant_hex = rgb_to_hex(tr_var, tg_var, tb_var)

        or_, og, ob = colorsys.hls_to_rgb(h_bg, 0.35, min(0.6, s_bg + 0.2))
        outline_hex = rgb_to_hex(or_, og, ob)

        ovr, ovg, ovb = colorsys.hls_to_rgb(h_bg, 0.20, min(0.6, s_bg))
        outline_variant_hex = rgb_to_hex(ovr, ovg, ovb)

    overrides = {
        "primary": hex_code.lower(),
        "primary_fixed": hex_code.lower(),
        "primary_fixed_dim": hex_code.lower(),
        "primary_container": hex_code.lower(),
        "source_color": hex_code.lower(),
        "on_primary": on_primary_hex,
        "on_primary_fixed": on_primary_hex,
        "on_primary_container": on_primary_hex,
        "background": bg_hex,
        "on_background": on_surface_hex,
        "on_surface": on_surface_hex,
        "on_surface_variant": on_surface_variant_hex,
        "outline": outline_hex,
        "outline_variant": outline_variant_hex,
        **ramp,
    }

    for role, color_val in overrides.items():
        if role in data["colors"]:
            for sub in ("dark", "light", "default"):
                if sub in data["colors"][role]:
                    data["colors"][role][sub]["color"] = color_val

    data["mode"] = mode
    data["is_dark_mode"] = (mode == "dark")
    return data


def snapshot_render_inputs() -> dict[str, Any]:
    """
    Captures input state without invoking compiler subprocesses.
    Reads enabled templates, input template bytes/hashes, and matugen tool signature.
    Enforces uniqueness of output destination basenames and template readability.
    """
    if not LIVE_CONFIG.is_file():
        raise FileNotFoundError(f"Matugen config file not found: {LIVE_CONFIG}")

    with LIVE_CONFIG.open("rb") as f:
        data = tomllib.load(f)

    raw_templates = data.get("templates", {})
    enabled_templates: dict[str, dict[str, str]] = {}
    dest_to_keys: dict[str, list[str]] = {}

    for key, val in raw_templates.items():
        out = val.get("output_path", "")
        if out and out != "/dev/null":
            out_name = Path(os.path.expanduser(out)).name
            enabled_templates[key] = val
            dest_to_keys.setdefault(out_name, []).append(key)

    duplicates = {dest: keys for dest, keys in dest_to_keys.items() if len(keys) > 1}
    if duplicates:
        raise ValueError(f"Duplicate output destination basenames in templates: {duplicates}")

    tmpl_hasher = hashlib.sha256()
    tmpl_bytes: dict[str, bytes] = {}

    for key in sorted(enabled_templates.keys()):
        tmpl = enabled_templates[key]
        in_path = Path(os.path.expanduser(tmpl.get("input_path", "")))
        out_name = Path(os.path.expanduser(tmpl["output_path"])).name
        if not in_path.is_file():
            raise FileNotFoundError(f"Template input file not found for '{key}': {in_path}")
        content = in_path.read_bytes()
        tmpl_bytes[key] = content
        tmpl_hasher.update(key.encode("utf-8"))
        tmpl_hasher.update(out_name.encode("utf-8"))
        tmpl_hasher.update(content)

    templates_digest = tmpl_hasher.hexdigest()

    matugen_path = shutil.which("matugen") or "/usr/bin/matugen"
    p = Path(matugen_path).resolve()
    matugen_available = p.is_file() and os.access(p, os.X_OK)
    matugen_path = str(p)
    mat_stat_str = ""
    mat_stat = None
    matugen_version = ""

    if matugen_available:
        st = p.stat()
        mat_stat = st
        mat_stat_str = f"{p}:{st.st_dev}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}"
        try:
            ver_res = subprocess.run([str(p), "--version"], capture_output=True, text=True, timeout=5)
            if ver_res.returncode == 0:
                matugen_version = ver_res.stdout.strip().replace("matugen", "").strip()
        except Exception:
            pass

    expected_outputs = frozenset([f"generated/{out_name}" for out_name in dest_to_keys.keys()] + ["palette.json"])

    return {
        "compiler_version": COMPILER_VERSION,
        "enabled_templates": enabled_templates,
        "tmpl_bytes": tmpl_bytes,
        "templates_digest": templates_digest,
        "expected_outputs": expected_outputs,
        "matugen_path": matugen_path,
        "matugen_available": matugen_available,
        "matugen_version": matugen_version,
        "mat_stat": mat_stat,
        "mat_stat_str": mat_stat_str,
    }


def compute_source_render_digest(preset: PresetDict, snapshot: dict[str, Any]) -> str:
    """
    Computes tool-independent source digest over render fields, compiler revision,
    and enabled template contents. Excludes local executable signature.
    """
    render_fields = {
        "id": preset["id"],
        "hex": preset["hex"].upper(),
        "bg_hex": preset["bg_hex"].lower(),
        "mode": preset.get("mode", "dark"),
        "scheme": preset.get("scheme", "scheme-fidelity"),
    }
    hasher = hashlib.sha256()
    hasher.update(COMPILER_VERSION.encode("utf-8"))
    hasher.update(json.dumps(render_fields, sort_keys=True).encode("utf-8"))
    hasher.update(snapshot["templates_digest"].encode("utf-8"))
    return hasher.hexdigest()


def compute_render_fingerprint(
    preset: PresetDict, snapshot: dict[str, Any] | None = None
) -> tuple[str, str]:
    """
    Computes full deterministic input fingerprint combining source_render_digest
    and Matugen tool stat signature.
    """
    if snapshot is None:
        snapshot = snapshot_render_inputs()

    source_render_digest = compute_source_render_digest(preset, snapshot)

    full_hasher = hashlib.sha256()
    full_hasher.update(source_render_digest.encode("utf-8"))
    full_hasher.update(snapshot["mat_stat_str"].encode("utf-8"))
    input_fingerprint = full_hasher.hexdigest()[:16]

    return input_fingerprint, source_render_digest


def validate_rendered_payloads(payloads: dict[str, bytes], preset: PresetDict) -> None:
    """Validates structural correctness of critical generated payloads at the bake boundary."""
    palette_raw = payloads.get("palette.json")
    if not palette_raw:
        raise ValueError("palette.json missing from generated payloads")
    palette = json.loads(palette_raw.decode("utf-8"))
    if not isinstance(palette, dict) or "colors" not in palette:
        raise ValueError("palette.json is missing required 'colors' object")
    if palette.get("mode") != preset["mode"]:
        raise ValueError(f"palette.json mode '{palette.get('mode')}' does not match preset mode '{preset['mode']}'")

    for name in ("generated/qt5ct-colors.conf", "generated/qt6ct-colors.conf"):
        qt_bytes = payloads.get(name)
        if qt_bytes is not None:
            text = qt_bytes.decode("utf-8", errors="replace")
            if not text.strip() or ("[" not in text or "]" not in text):
                raise ValueError(f"{name} is not a valid INI color scheme")

    for path, data in payloads.items():
        if path.endswith(".json") and path != "palette.json":
            try:
                json.loads(data.decode("utf-8"))
            except Exception as e:
                raise ValueError(f"Payload '{path}' failed JSON validation: {e}")
        elif path.endswith(".xml") or path.endswith(".svg"):
            try:
                ET.fromstring(data.decode("utf-8"))
            except Exception as e:
                raise ValueError(f"Payload '{path}' failed XML validation: {e}")
        elif path.endswith(".toml"):
            try:
                tomllib.loads(data.decode("utf-8"))
            except Exception as e:
                raise ValueError(f"Payload '{path}' failed TOML validation: {e}")
        elif path.endswith(".css"):
            css_text = data.decode("utf-8", errors="replace")
            if css_text.count("{") != css_text.count("}"):
                raise ValueError(f"Payload '{path}' has unbalanced CSS braces")


def read_verified_pack(
    pack_dir: Path,
    expected_fingerprint: str | None = None,
    expected_source_digest: str | None = None,
    expected_outputs: frozenset[str] | None = None,
    preset: PresetDict | None = None,
) -> tuple[bool, dict[str, bytes], dict[str, Any], str]:
    """
    Verifies that pack_dir contains a valid manifest.json, exact expected output inventory,
    matching SHA-256 hashes, and valid payload structures. Returns payload bytes in memory.
    """
    manifest_file = pack_dir / "manifest.json"
    if not manifest_file.is_file():
        return False, {}, {}, "manifest.json missing"

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            return False, {}, {}, "manifest.json invalid format"

        if manifest.get("format_version") != 2:
            return False, {}, manifest, f"Unsupported format_version: {manifest.get('format_version')}"

        if expected_fingerprint and manifest.get("input_fingerprint") != expected_fingerprint:
            return False, {}, manifest, f"Fingerprint mismatch: expected {expected_fingerprint}, got {manifest.get('input_fingerprint')}"

        if expected_source_digest and manifest.get("source_render_digest") != expected_source_digest:
            return False, {}, manifest, f"Source render digest mismatch: expected {expected_source_digest}, got {manifest.get('source_render_digest')}"

        if preset:
            if manifest.get("id") != preset["id"]:
                return False, {}, manifest, f"Preset ID mismatch: expected {preset['id']}, got {manifest.get('id')}"
            if manifest.get("mode") != preset["mode"]:
                return False, {}, manifest, f"Mode mismatch: expected {preset['mode']}, got {manifest.get('mode')}"

        outputs = manifest.get("outputs", [])
        if not outputs:
            return False, {}, manifest, "manifest.json has empty outputs"

        manifest_paths = frozenset(out.get("path", "") for out in outputs)
        if expected_outputs and manifest_paths != expected_outputs:
            missing = expected_outputs - manifest_paths
            extra = manifest_paths - expected_outputs
            return False, {}, manifest, f"Inventory mismatch (missing: {missing}, extra: {extra})"

        payloads: dict[str, bytes] = {}
        for out in outputs:
            rel_path = out.get("path", "")
            f_path = pack_dir / rel_path
            if not f_path.is_file():
                return False, {}, manifest, f"Output file missing: {rel_path}"
            if f_path.stat().st_size != out.get("size"):
                return False, {}, manifest, f"Size mismatch for {rel_path}"
            data = f_path.read_bytes()
            if hashlib.sha256(data).hexdigest() != out.get("sha256"):
                return False, {}, manifest, f"SHA-256 checksum mismatch for {rel_path}"
            payloads[rel_path] = data

        if preset:
            validate_rendered_payloads(payloads, preset)

        return True, payloads, manifest, "Verified"
    except Exception as e:
        return False, {}, {}, f"Verification error: {e}"


def is_pack_valid(
    preset: PresetDict, snapshot: dict[str, Any] | None = None
) -> tuple[bool, Path | None, str, dict[str, bytes], str]:
    """
    Checks whether an existing compiled pack matches current inputs and passes integrity verification.
    If Matugen tool is absent, inspects retained revisions matching source_render_digest.
    """
    if snapshot is None:
        snapshot = snapshot_render_inputs()

    pid = preset["id"]
    fingerprint, source_render_digest = compute_render_fingerprint(preset, snapshot)
    pack_dir = PACKS_ROOT / pid / fingerprint

    if pack_dir.is_dir():
        ok, payloads, manifest, msg = read_verified_pack(
            pack_dir,
            expected_fingerprint=fingerprint,
            expected_source_digest=source_render_digest,
            expected_outputs=snapshot["expected_outputs"],
            preset=preset,
        )
        if ok:
            return True, pack_dir, fingerprint, payloads, f"Verified ({fingerprint})"
        return False, None, fingerprint, {}, f"Corrupt pack ({msg})"

    # Fallback when Matugen tool is absent: scan candidate revisions matching source_render_digest
    if not snapshot.get("matugen_available"):
        base_dir = PACKS_ROOT / pid
        if base_dir.is_dir():
            candidates: list[tuple[int, Path, dict[str, Any]]] = []
            for d in base_dir.iterdir():
                mf_file = d / "manifest.json"
                if d.is_dir() and mf_file.is_file():
                    try:
                        mf = json.loads(mf_file.read_text(encoding="utf-8"))
                        if (
                            mf.get("source_render_digest") == source_render_digest
                            and mf.get("mode") == preset["mode"]
                        ):
                            candidates.append((mf.get("built_at_ns", 0), d, mf))
                    except Exception:
                        pass

            candidates.sort(key=lambda x: x[0], reverse=True)
            for _, cand_dir, mf in candidates:
                cand_fp = mf.get("input_fingerprint", cand_dir.name)
                ok, payloads, _, _ = read_verified_pack(
                    cand_dir,
                    expected_fingerprint=cand_fp,
                    expected_source_digest=source_render_digest,
                    expected_outputs=snapshot["expected_outputs"],
                    preset=preset,
                )
                if ok:
                    return True, cand_dir, cand_fp, payloads, "Applied cached revision; Matugen freshness unavailable"

    return False, None, fingerprint, {}, "Pack not found in cache"


def prune_preset_revisions(preset_id: str, keep_fingerprints: set[str]) -> None:
    """Retains current and at most 1 previous verified revision under PACKS_ROOT/<preset_id>."""
    base_dir = PACKS_ROOT / preset_id
    if not base_dir.is_dir():
        return

    valid_predecessors: list[tuple[int, Path]] = []
    for d in list(base_dir.iterdir()):
        if not d.is_dir():
            continue
        if d.name in keep_fingerprints:
            continue
        ok, _, mf, _ = read_verified_pack(d)
        if ok and mf.get("format_version") == 2:
            built_at = int(mf.get("built_at_ns", d.stat().st_mtime_ns))
            valid_predecessors.append((built_at, d))
            continue
        # Corrupt or untracked directory
        shutil.rmtree(d, ignore_errors=True)

    valid_predecessors.sort(key=lambda x: x[0], reverse=True)
    # Keep at most 1 previous verified revision
    for _, old_dir in valid_predecessors[1:]:
        shutil.rmtree(old_dir, ignore_errors=True)


def _atomic_write_json(target_path: Path, data: dict[str, Any]) -> None:
    """Writes JSON data atomically via a unique temporary file in the same directory."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f"{target_path.stem}-", dir=target_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp_path, target_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def update_catalog_entry(preset: PresetDict, fingerprint: str) -> None:
    """Updates catalog.json atomically as an optional index summary."""
    try:
        catalog_data: dict[str, Any] = {"format_version": 2, "presets": {}}
        if CATALOG_PATH.is_file():
            try:
                raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("presets"), dict):
                    catalog_data = raw
            except Exception:
                pass

        pid = preset["id"]
        catalog_data["presets"][pid] = {
            "input_fingerprint": fingerprint,
            "pack_path": f"packs/{pid}/{fingerprint}",
            "mode": preset.get("mode", "dark"),
            "built_at_ns": time.time_ns(),
        }
        _atomic_write_json(CATALOG_PATH, catalog_data)
    except Exception as e:
        print(f"Warning: Failed to update catalog summary: {e}", file=sys.stderr)


def reconstruct_catalog(presets: list[PresetDict], snapshot: dict[str, Any] | None = None) -> None:
    """
    Reconstructs minimal catalog summary from current valid definitions
    and verified pack revisions. Prunes stale pointers and records relative pack paths.
    """
    if snapshot is None:
        try:
            snapshot = snapshot_render_inputs()
        except Exception:
            return
    catalog_data: dict[str, Any] = {"format_version": 2, "presets": {}}
    for p in presets:
        pid = p["id"]
        fp, _ = compute_render_fingerprint(p, snapshot)
        pack_dir = PACKS_ROOT / pid / fp
        if pack_dir.is_dir():
            catalog_data["presets"][pid] = {
                "input_fingerprint": fp,
                "pack_path": f"packs/{pid}/{fp}",
                "mode": p.get("mode", "dark"),
            }
    try:
        _atomic_write_json(CATALOG_PATH, catalog_data)
    except Exception as e:
        print(f"Warning: Failed to reconstruct catalog summary: {e}", file=sys.stderr)


def compile_pack(
    preset: PresetDict, snapshot: dict[str, Any] | None = None
) -> tuple[Path, str, dict[str, bytes]]:
    """
    Compiles a single preset pack inside .staging using private staged templates
    and atomically publishes to packs/<id>/<fingerprint>.
    """
    if snapshot is None:
        snapshot = snapshot_render_inputs()

    if not snapshot.get("matugen_available"):
        raise RuntimeError(f"Matugen executable '{snapshot['matugen_path']}' is not available to compile")

    pid = preset["id"]
    mode = preset.get("mode", "dark")
    fingerprint, source_digest = compute_render_fingerprint(preset, snapshot)
    dest_dir = PACKS_ROOT / pid / fingerprint
    enabled_templates = snapshot["enabled_templates"]

    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    stage_tmp = tempfile.mkdtemp(prefix=f"stage-{pid}-", dir=STAGING_ROOT)
    stage_dir = Path(stage_tmp)

    try:
        gen_dir = stage_dir / "generated"
        gen_dir.mkdir(parents=True, exist_ok=True)

        # 1. Private template staging (guarantees rendered bytes match hashed bytes)
        stage_tmpl_dir = stage_dir / "input_templates"
        stage_tmpl_dir.mkdir(parents=True, exist_ok=True)

        bake_cfg_path = stage_dir / "matugen_render.toml"
        cfg_lines = ["[config]", "[templates]"]

        for key, tmpl in enabled_templates.items():
            content = snapshot["tmpl_bytes"][key]
            in_file_name = f"{key}_{Path(tmpl['input_path']).name}"
            stage_in_file = stage_tmpl_dir / in_file_name
            stage_in_file.write_bytes(content)

            out_name = Path(os.path.expanduser(tmpl["output_path"])).name
            dest_file = gen_dir / out_name

            cfg_lines.append(f"[templates.{json.dumps(key)}]")
            cfg_lines.append(f"input_path = {json.dumps(str(stage_in_file))}")
            cfg_lines.append(f"output_path = {json.dumps(str(dest_file))}")

        bake_cfg_path.write_text("\n".join(cfg_lines), encoding="utf-8")

        # 2. Palette JSON with private minimal configuration
        pal_cfg_path = stage_dir / "matugen_palette.toml"
        pal_cfg_path.write_text("[config]\n[templates]\n", encoding="utf-8")
        palette_data = generate_preset_palette(
            preset,
            matugen_path=snapshot["matugen_path"],
            config_path=pal_cfg_path,
        )
        pal_cfg_path.unlink(missing_ok=True)
        palette_path = stage_dir / "palette.json"
        palette_bytes = (json.dumps(palette_data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        palette_path.write_bytes(palette_bytes)

        # 3. Render templates via Matugen subprocess
        cmd = [
            snapshot["matugen_path"],
            "--config",
            str(bake_cfg_path),
            "--mode",
            mode,
            "json",
            str(palette_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise RuntimeError(f"Matugen render failed for {pid}: {res.stderr.strip()}")

        # 4. Inventory, hash, and validate outputs
        outputs = []
        payloads: dict[str, bytes] = {}
        for f in sorted(gen_dir.iterdir()):
            if f.is_file():
                f_bytes = f.read_bytes()
                rel_p = f"generated/{f.name}"
                payloads[rel_p] = f_bytes
                outputs.append({
                    "path": rel_p,
                    "size": len(f_bytes),
                    "sha256": hashlib.sha256(f_bytes).hexdigest(),
                })

        payloads["palette.json"] = palette_bytes
        outputs.append({
            "path": "palette.json",
            "size": len(palette_bytes),
            "sha256": hashlib.sha256(palette_bytes).hexdigest(),
        })

        generated_set = frozenset(payloads.keys())
        if generated_set != snapshot["expected_outputs"]:
            missing = snapshot["expected_outputs"] - generated_set
            raise RuntimeError(f"Compilation for {pid} failed to produce expected outputs: {missing}")

        validate_rendered_payloads(payloads, preset)

        # 5. Manifest v2
        mat_stat = snapshot.get("mat_stat")
        manifest = {
            "format_version": 2,
            "id": pid,
            "label": preset["label"],
            "hex": preset["hex"],
            "bg_hex": preset["bg_hex"],
            "mode": mode,
            "scheme": preset.get("scheme", "scheme-fidelity"),
            "input_fingerprint": fingerprint,
            "source_render_digest": source_digest,
            "built_at_ns": time.time_ns(),
            "matugen_version": snapshot.get("matugen_version", ""),
            "tool_signature": {
                "path": snapshot["matugen_path"],
                "version": snapshot.get("matugen_version", ""),
                "device": mat_stat.st_dev if mat_stat else 0,
                "inode": mat_stat.st_ino if mat_stat else 0,
                "size": mat_stat.st_size if mat_stat else 0,
                "mtime_ns": mat_stat.st_mtime_ns if mat_stat else 0,
                "ctime_ns": mat_stat.st_ctime_ns if mat_stat else 0,
            },
            "outputs": outputs,
        }
        (stage_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        bake_cfg_path.unlink(missing_ok=True)
        shutil.rmtree(stage_tmpl_dir, ignore_errors=True)

        # 6. Pre-publication recheck
        recheck_preset = vivid_data.load_preset(pid)
        if recheck_preset != preset:
            raise RuntimeError(f"Preset definition '{pid}' changed during compilation; aborted staging")
        p = Path(snapshot["matugen_path"])
        if p.is_file():
            st = p.stat()
            cur_stat = f"{p}:{st.st_dev}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}"
            if cur_stat != snapshot["mat_stat_str"]:
                raise RuntimeError("Matugen tool executable signature changed during compilation; aborted staging")

        # 7. Atomic publication / Same-fingerprint comparison
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        if dest_dir.exists():
            ok_existing, existing_payloads, _, _ = read_verified_pack(
                dest_dir,
                expected_fingerprint=fingerprint,
                expected_source_digest=source_digest,
                expected_outputs=snapshot["expected_outputs"],
                preset=preset,
            )
            if ok_existing:
                hashes_match = (payloads == existing_payloads)
                if hashes_match:
                    shutil.rmtree(stage_dir, ignore_errors=True)
                    prune_preset_revisions(pid, keep_fingerprints={fingerprint})
                    return dest_dir, fingerprint, payloads
                else:
                    raise RuntimeError(f"Nondeterministic build for {pid} at fingerprint {fingerprint}: output hashes differ")
            else:
                quarantine = STAGING_ROOT / f"quarantine-{pid}-{time.time_ns()}"
                os.replace(dest_dir, quarantine)
                os.replace(stage_dir, dest_dir)
                shutil.rmtree(quarantine, ignore_errors=True)
        else:
            os.replace(stage_dir, dest_dir)

        prune_preset_revisions(pid, keep_fingerprints={fingerprint})
        return dest_dir, fingerprint, payloads
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)


def ensure_pack(
    preset_id: str, rebuild: bool = False, snapshot: dict[str, Any] | None = None
) -> tuple[bool, PreparedPack | None, str]:
    """
    Ensures a valid pack exists for preset_id.
    Returns (ok, prepared_pack, message). Loads definition once and returns canonical PreparedPack.
    """
    preset, diag = vivid_data.load_preset_with_diagnostic(preset_id)
    if not preset:
        return False, None, diag or f"Unknown preset ID: '{preset_id}'"

    if snapshot is None:
        try:
            snapshot = snapshot_render_inputs()
        except Exception as e:
            return False, None, f"Failed to snapshot render inputs: {e}"

    if not snapshot["enabled_templates"]:
        return False, None, f"No enabled templates found in {LIVE_CONFIG}"

    try:
        with cache_lock():
            # Re-verify definition under lock
            rechecked_preset, _ = vivid_data.load_preset_with_diagnostic(preset_id)
            if rechecked_preset:
                preset = rechecked_preset

            if not rebuild:
                valid, pack_dir, fp, payloads, msg = is_pack_valid(preset, snapshot)
                if valid and pack_dir:
                    prep = PreparedPack(
                        id=preset["id"],
                        label=preset["label"],
                        hex=preset["hex"],
                        bg_hex=preset["bg_hex"],
                        mode=preset["mode"],
                        scheme=preset["scheme"],
                        order=preset["order"],
                        desc=preset["desc"],
                        fingerprint=fp,
                        source_render_digest=compute_source_render_digest(preset, snapshot),
                        pack_dir=pack_dir,
                        payloads=payloads,
                        message=msg,
                    )
                    return True, prep, msg

            pack_dir, fp, payloads = compile_pack(preset, snapshot)
            update_catalog_entry(preset, fp)
            prep = PreparedPack(
                id=preset["id"],
                label=preset["label"],
                hex=preset["hex"],
                bg_hex=preset["bg_hex"],
                mode=preset["mode"],
                scheme=preset["scheme"],
                order=preset["order"],
                desc=preset["desc"],
                fingerprint=fp,
                source_render_digest=compute_source_render_digest(preset, snapshot),
                pack_dir=pack_dir,
                payloads=payloads,
                message=f"Compiled pack '{preset['id']}' ({fp})",
            )
            return True, prep, f"Successfully compiled pack '{preset['id']}' ({fp})"
    except Exception as e:
        return False, None, f"Failed to compile pack '{preset_id}': {e}"


def check_all() -> int:
    """Verifies integrity of all cached packs against source definitions without compiling."""
    presets, diagnostics = vivid_data.load_presets_and_diagnostics()
    if diagnostics:
        print("[-] Source definition errors found:", file=sys.stderr)
        for d in diagnostics:
            print(f"    - {d}", file=sys.stderr)
        return 1

    try:
        snapshot = snapshot_render_inputs()
    except Exception as e:
        print(f"[-] Input error: {e}", file=sys.stderr)
        return 1

    print(f":: Checking {len(presets)} vivid preset packs in {CACHE_ROOT}...")
    valid_count = 0
    missing_count = 0
    corrupt_count = 0

    for idx, p in enumerate(presets, 1):
        pid = p["id"]
        with cache_lock():
            valid, pack_dir, fp, payloads, msg = is_pack_valid(p, snapshot)

        if valid and pack_dir:
            file_count = len(payloads)
            print(f"   [{idx:02d}/{len(presets):02d}] {p['label']:<20} ({pid}): [VALID] {fp} ({file_count} files)")
            valid_count += 1
        elif "Corrupt" in msg:
            print(f"   [{idx:02d}/{len(presets):02d}] {p['label']:<20} ({pid}): [CORRUPT] {msg}")
            corrupt_count += 1
        else:
            print(f"   [{idx:02d}/{len(presets):02d}] {p['label']:<20} ({pid}): [MISSING] {msg}")
            missing_count += 1

    print(f"\n:: Check summary: {valid_count} valid, {missing_count} missing, {corrupt_count} corrupt.")
    return 0 if valid_count == len(presets) else 1


def validate_all() -> int:
    """Read-only validation of preset definitions and template sources."""
    print(":: Validating vivid preset definitions...")
    errors = vivid_data.validate_all_definitions()
    if errors:
        print(f"[-] Definition validation failed with {len(errors)} error(s):", file=sys.stderr)
        for err in errors:
            print(f"    - {err}", file=sys.stderr)
        return 1

    presets = vivid_data.load_presets()
    print(f"[OK] All {len(presets)} preset definitions passed schema and uniqueness validation.")

    print(":: Validating enabled template files...")
    try:
        snapshot = snapshot_render_inputs()
    except Exception as e:
        print(f"[-] Template validation error: {e}", file=sys.stderr)
        return 1

    print(f"[OK] All {len(snapshot['enabled_templates'])} active template sources exist on disk.")
    return 0


def prune_all() -> int:
    """Explicitly prunes obsolete revisions, removed preset IDs, and interrupted staging leftovers."""
    with cache_lock():
        presets, _ = vivid_data.load_presets_and_diagnostics()
        valid_ids = {p["id"] for p in presets}
        existing_definition_stems = {f.stem for f in PRESETS_DIR.glob("*.json")} if PRESETS_DIR.is_dir() else set()

        try:
            snapshot = snapshot_render_inputs()
        except Exception:
            snapshot = None

        pruned_dirs = 0
        if PACKS_ROOT.is_dir():
            for p_dir in list(PACKS_ROOT.iterdir()):
                if not p_dir.is_dir():
                    continue
                # Do NOT remove if source file exists on disk even if temporarily malformed
                if p_dir.name not in existing_definition_stems and p_dir.name not in valid_ids:
                    print(f":: Removing obsolete preset directory: {p_dir.name}")
                    shutil.rmtree(p_dir, ignore_errors=True)
                    pruned_dirs += 1
                elif snapshot and p_dir.name in valid_ids:
                    preset = next((p for p in presets if p["id"] == p_dir.name), None)
                    if preset:
                        fp, _ = compute_render_fingerprint(preset, snapshot)
                        prune_preset_revisions(p_dir.name, keep_fingerprints={fp})

        # Clean staging / quarantine leftovers
        if STAGING_ROOT.is_dir():
            for s_dir in list(STAGING_ROOT.iterdir()):
                shutil.rmtree(s_dir, ignore_errors=True)
                pruned_dirs += 1

        # Reconstruct catalog summary
        if snapshot:
            reconstruct_catalog(presets, snapshot)

        print(f":: Pruning complete. Cleaned {pruned_dirs} obsolete or temporary directories.")
    return 0


def bake_all(*, rebuild: bool = False) -> int:
    """Compiles all discovered presets into the cache root, releasing lock between presets."""
    presets, diagnostics = vivid_data.load_presets_and_diagnostics()
    if diagnostics:
        print("[-] Aborting bake_all: source definition errors found:", file=sys.stderr)
        for d in diagnostics:
            print(f"    - {d}", file=sys.stderr)
        return 1

    if not presets:
        print(f"[-] Error: no presets found in {vivid_data.PRESETS_DIR}", file=sys.stderr)
        return 1

    try:
        snapshot = snapshot_render_inputs()
    except Exception as e:
        print(f"[-] Input error: {e}", file=sys.stderr)
        return 1

    print(f":: Loaded {len(snapshot['enabled_templates'])} active template definitions.")
    print(f":: Preparing {len(presets)} vivid preset packs into {CACHE_ROOT}...")

    success_count = 0
    start_all = time.perf_counter_ns()

    for idx, p in enumerate(presets, 1):
        pid = p["id"]
        lbl = p["label"]
        print(f"   [{idx:02d}/{len(presets):02d}] {lbl} ({pid})...", end="", flush=True)

        ok, prep, msg = ensure_pack(pid, rebuild=rebuild, snapshot=snapshot)
        if ok and prep:
            print(" [OK]")
            success_count += 1
        else:
            print(f" [FAILED: {msg}]")

    # Recheck definitions at end of batch
    final_presets, final_diags = vivid_data.load_presets_and_diagnostics()
    if final_diags or [p["id"] for p in final_presets] != [p["id"] for p in presets]:
        print("[-] Warning: Preset definitions changed during batch preparation; catalog may not be fully current.", file=sys.stderr)
        return 1

    # Reconstruct catalog summary to ensure completeness and remove stale pointers
    reconstruct_catalog(presets, snapshot)

    elapsed_ms = (time.perf_counter_ns() - start_all) / 1_000_000
    print(f"\n:: Completed: {success_count}/{len(presets)} packs prepared in {elapsed_ms:.1f}ms")
    return 0 if success_count == len(presets) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Dusky Vivid Theme Pack Baker (v2.1)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--preset", "-p", help="Bake a single preset by ID")
    group.add_argument("--all", "-a", action="store_true", help="Bake all presets (default)")
    group.add_argument("--check", action="store_true", help="Check integrity of cached packs without baking")
    group.add_argument("--validate", action="store_true", help="Validate definition schemas and template sources")
    group.add_argument("--prune", action="store_true", help="Prune obsolete revisions and staging leftovers")

    parser.add_argument("--rebuild", action="store_true", help="Force rebuild even if cache matches fingerprint")
    parser.add_argument("--force", action="store_true", help="Compatibility flag for noninteractive setup orchestrator")
    args = parser.parse_args()

    if args.validate:
        return validate_all()

    if args.check:
        return check_all()

    if args.prune:
        return prune_all()

    if args.preset:
        ok, _, msg = ensure_pack(args.preset, rebuild=args.rebuild)
        print(f":: {msg}")
        return 0 if ok else 1

    return bake_all(rebuild=args.rebuild)


if __name__ == "__main__":
    sys.exit(main())
