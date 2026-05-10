#!/usr/bin/env python3
import os
import re
import json
import subprocess
import colorsys
import tempfile
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Literal, Dict

from textual import on, events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.widgets import (
    Label, Input, ListView, ListItem, 
    Tabs, TabbedContent, TabPane, OptionList
)
from textual.widgets.option_list import Option
from textual.screen import ModalScreen
from textual.reactive import reactive
from textual.theme import Theme
from textual.timer import Timer

from rich.text import Text

# =============================================================================
# HYPRLAND LUA ENGINE (0.55+ COMPATIBLE - MULTI-FILE SUPPORT)
# =============================================================================

class HyprlandLuaEngine:
    """
    Backend Engine for Hyprland 0.55+ Lua configurations.
    Tracks all split modules (dofile/require) and uses an embedded 
    lexical Lua tokenizer to surgically mutate values across multiple files.
    """
    def __init__(self, config_path: str = "~/.config/hypr/hyprland.lua"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.lua_bin = self._find_lua()
        self.cache: Dict[str, Any] = {}
        self.loaded_files: list[str] = []
        
        # Guarantee a base file exists for the template
        if not self.config_path.exists():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text("hl.config({\n    general = {\n        border_size = 2\n    }\n})\n")

    def _find_lua(self) -> str:
        for cmd in ["lua5.4", "lua54", "lua"]:
            try:
                res = subprocess.run([cmd, "-e", "assert(_VERSION:match('5%.[4-9]'))"], 
                                     capture_output=True, text=True)
                if res.returncode == 0:
                    return cmd
            except FileNotFoundError:
                continue
        raise RuntimeError("Lua 5.4+ not found. Hyprland 0.55+ Lua configs require Lua 5.4.")

    def load_state(self) -> Dict[str, Any]:
        """Evaluates the config, tracks dofile imports, and returns flattened state."""
        if not self.config_path.exists():
            return {}

        lua_evaluator = """
        local main_path = arg[1]
        local config_root = {}
        local loaded_files = {main_path}
        
        local function deep_merge(dst, src)
            for k, v in pairs(src) do
                if type(v) == "table" then
                    if type(dst[k]) ~= "table" then dst[k] = {} end
                    deep_merge(dst[k], v)
                else
                    dst[k] = v
                end
            end
            return dst
        end
        
        local inert_proxy = setmetatable({}, { __index = function() return setmetatable({}, { __call = function() return {} end }) end })
        local hl = setmetatable({}, { __index = function() return inert_proxy end })
        hl.config = function(tbl) if type(tbl) == "table" then deep_merge(config_root, tbl) end end

        local safe_env = { hl = hl, math = math, string = string, table = table, type = type, pairs = pairs, ipairs = ipairs, tostring = tostring, tonumber = tonumber, print = print, os = os, io = io }
        safe_env._G = safe_env

        -- Hook dofile to track modular split configs
        local original_dofile = dofile
        safe_env.dofile = function(path)
            table.insert(loaded_files, path)
            local chunk, err = loadfile(path, "t", safe_env)
            if chunk then return chunk() else return nil end
        end

        local chunk, err = loadfile(main_path, "t", safe_env)
        if chunk then pcall(chunk) end

        -- Export state and file registry as JSON
        local out_state = {}
        local function escape_str(s) return '"' .. s:gsub('\\\\', '\\\\\\\\'):gsub('"', '\\\\"'):gsub('\\n', '\\\\n') .. '"' end
        local function walk(t, scope)
            for k, v in pairs(t) do
                if type(k) == "string" then
                    local new_scope = scope == "" and k or (scope .. "/" .. k)
                    if type(v) == "table" then walk(v, new_scope)
                    else table.insert(out_state, escape_str(new_scope) .. ":" .. escape_str(tostring(v))) end
                end
            end
        end
        walk(config_root, "")
        
        local out_files = {}
        for _, f in ipairs(loaded_files) do table.insert(out_files, escape_str(f)) end

        print('{"state": {' .. table.concat(out_state, ",") .. '}, "files": [' .. table.concat(out_files, ",") .. ']}')
        """
        try:
            res = subprocess.run([self.lua_bin, "-", str(self.config_path)], 
                                 input=lua_evaluator, text=True, capture_output=True, timeout=3)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                self.cache = data.get("state", {})
                self.loaded_files = data.get("files", [str(self.config_path)])
                return self.cache
        except Exception as e:
            print(f"[Engine Error] Failed to load state: {e}")
        return {}

    def write_value(self, target_key: str, target_scope: str, new_value: Any) -> bool:
        if not self.loaded_files:
            self.loaded_files = [str(self.config_path)]
            
        if new_value == "__DELETE__":
            val_str = "__DELETE__"
        elif isinstance(new_value, bool):
            val_str = "true" if new_value else "false"
        elif isinstance(new_value, (int, float)):
            val_str = str(new_value)
        else:
            val_str = f'"{new_value}"'

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as vf:
            vf.write(val_str)
            val_file_path = vf.name

        # Exact Lexical Scanner imported from your bash script for maximum safety
        lua_mutator = """
        local src_path = assert(arg[1], "missing source")
        local target_key = assert(arg[2], "missing key")
        local target_scope = assert(arg[3], "missing scope")
        local val_path = assert(arg[4], "missing value file")
        local max_source_bytes = 16 * 1024 * 1024

        local function die(code, msg) io.stderr:write(tostring(msg), "\\n"); os.exit(code) end
        local function read_file(path, code)
            local f, err = io.open(path, "rb")
            if not f then die(code, err or "open failed") end
            local s, read_err = f:read("*a")
            f:close()
            if not s then die(code, read_err or "read failed") end
            if #s > max_source_bytes then die(code, "source exceeds size limit") end
            return s
        end

        local text = read_file(src_path, 4)
        local new_value = read_file(val_path, 4)
        local len = #text
        local tokens = {}
        local pos = 1

        local function is_alpha(c) return c:match("^[A-Za-z_]$") ~= nil end
        local function is_alnum(c) return c:match("^[A-Za-z0-9_]$") ~= nil end
        local function is_space(c) return c == " " or c == "\\t" or c == "\\r" or c == "\\n" end
        local function add(tp, val, s, e) tokens[#tokens + 1] = { type = tp, val = val, s = s, e = e } end

        local function long_bracket_end_at(p)
            if text:sub(p, p) ~= "[" then return nil end
            local q = p + 1
            while q <= len and text:sub(q, q) == "=" do q = q + 1 end
            if text:sub(q, q) ~= "[" then return nil end
            local eqs = text:sub(p + 1, q - 1)
            local close = "]" .. eqs .. "]"
            local found = text:find(close, q + 1, true)
            if not found then die(5, "unterminated long bracket") end
            return found + #close - 1
        end

        while pos <= len do
            local c = text:sub(pos, pos)
            if is_space(c) then pos = pos + 1
            elseif c == "-" and text:sub(pos + 1, pos + 1) == "-" then
                pos = pos + 2
                local lb_end = long_bracket_end_at(pos)
                if lb_end then pos = lb_end + 1
                else
                    local nl = text:find("\\n", pos, true)
                    if nl then pos = nl + 1 else pos = len + 1 end
                end
            elseif c == "'" or c == '"' then
                local quote = c
                local s = pos
                pos = pos + 1
                local closed = false
                while pos <= len do
                    local ch = text:sub(pos, pos)
                    if ch == "\\\\" then pos = pos + 2
                    elseif ch == quote then pos = pos + 1; closed = true; break
                    else pos = pos + 1 end
                end
                add("STRING", text:sub(s, pos - 1), s, pos - 1)
            elseif c == "[" then
                local lb_end = long_bracket_end_at(pos)
                if lb_end then add("STRING", text:sub(pos, lb_end), pos, lb_end); pos = lb_end + 1
                else add("LBRACK", c, pos, pos); pos = pos + 1 end
            elseif is_alpha(c) then
                local s = pos; pos = pos + 1
                while pos <= len and is_alnum(text:sub(pos, pos)) do pos = pos + 1 end
                add("IDENT", text:sub(s, pos - 1), s, pos - 1)
            elseif c:match("^[0-9]$") or (c == "." and text:sub(pos + 1, pos + 1):match("^[0-9]$")) then
                local s = pos; pos = pos + 1
                while pos <= len and text:sub(pos, pos):match("^[A-Za-z0-9_%.%+%-]$") do pos = pos + 1 end
                add("NUMBER", text:sub(s, pos - 1), s, pos - 1)
            else
                local map = { ["{"] = "LBRACE", ["}"] = "RBRACE", ["("] = "LPAREN", [")"] = "RPAREN", ["["] = "LBRACK", ["]"] = "RBRACK", ["="] = "EQUALS", [","] = "COMMA", [";"] = "SEMI", ["."] = "DOT" }
                add(map[c] or "OTHER", c, pos, pos); pos = pos + 1
            end
        end

        local function unquote_string(raw)
            local chunk, err = load("return " .. raw, "t", "t", {})
            if chunk then local ok, v = pcall(chunk); if ok and type(v)=="string" then return v end end
            return raw
        end
        local function trim(s) return (s:gsub("^%s+", ""):gsub("%s+$", "")) end
        local function is_lua_number_literal(raw) raw = trim(raw) return raw:match("^[+-]?%d+%.?%d*([eE][+-]?%d+)?$") or raw:match("^[+-]?0[xX][%da-fA-F]+$") end

        local function format_short_string(value, quote)
            local out = { quote }
            for i = 1, #value do
                local ch = value:sub(i, i)
                if ch == "\\\\" then out[#out+1] = "\\\\\\\\" elseif ch == "\\n" then out[#out+1] = "\\\\n" elseif ch == quote then out[#out+1] = "\\\\" .. quote else out[#out+1] = ch end
            end
            out[#out+1] = quote; return table.concat(out)
        end

        local function format_replacement(old_raw)
            if new_value == "__DELETE__" then return "nil" end
            local t = trim(old_raw)
            if t == "true" or t == "false" or t == "nil" then return new_value end
            if is_lua_number_literal(t) then return new_value end
            if t:match("^['\\"]") then return format_short_string(new_value, t:sub(1, 1)) end
            error("Target expression too complex to rewrite")
        end

        local matches = {}
        local function scope_string(parts) return table.concat(parts, "/") end

        local parse_table
        local function find_rhs_end(i)
            local j = i; local depth = 0; local block_depth = 0; local rhs_end = i
            while j <= #tokens do
                local tp = tokens[j].type; local val = tokens[j].val
                if tp == "IDENT" then
                    if val == "function" or val == "if" or val == "for" or val == "while" then block_depth = block_depth + 1
                    elseif val == "end" and block_depth > 0 then block_depth = block_depth - 1 end
                end
                if block_depth == 0 then
                    if tp == "LBRACE" or tp == "LPAREN" or tp == "LBRACK" then depth = depth + 1
                    elseif tp == "RBRACE" or tp == "RPAREN" or tp == "RBRACK" then
                        if depth == 0 then break end; depth = depth - 1
                    elseif depth == 0 and (tp == "COMMA" or tp == "SEMI") then break end
                end
                rhs_end = j; j = j + 1
            end
            return rhs_end, j
        end

        local function key_at(i)
            local tok = tokens[i]
            if not tok then return nil, i end
            if tok.type == "IDENT" and tokens[i + 1] and tokens[i + 1].type == "EQUALS" then return tok.val, i + 2 end
            if tok.type == "LBRACK" and tokens[i + 1] and tokens[i + 1].type == "STRING" and tokens[i + 2] and tokens[i + 2].type == "RBRACK" and tokens[i + 3] and tokens[i + 3].type == "EQUALS" then return unquote_string(tokens[i + 1].val), i + 4 end
            return nil, i
        end

        parse_table = function(i, scope_parts)
            if not tokens[i] or tokens[i].type ~= "LBRACE" then return i end
            i = i + 1
            while i <= #tokens do
                if tokens[i].type == "RBRACE" then return i + 1 end
                if tokens[i].type == "COMMA" or tokens[i].type == "SEMI" then i = i + 1 goto continue end

                local key, rhs = key_at(i)
                if key then
                    local rhs_end, next_i = find_rhs_end(rhs)
                    if tokens[rhs] and tokens[rhs].type == "LBRACE" then
                        scope_parts[#scope_parts + 1] = key
                        parse_table(rhs, scope_parts)
                        scope_parts[#scope_parts] = nil
                    else
                        local curr_scope = scope_string(scope_parts)
                        if key == target_key and curr_scope == target_scope then
                            local raw = text:sub(tokens[rhs].s, tokens[rhs_end].e)
                            matches[#matches + 1] = { s = tokens[rhs].s, e = tokens[rhs_end].e, raw = raw }
                        end
                    end
                    i = next_i
                else
                    local _, next_i = find_rhs_end(i)
                    if next_i <= i then next_i = i + 1 end
                    i = next_i
                end
                ::continue::
            end
            return i
        end

        local function config_arg_index(i)
            if not (tokens[i] and tokens[i].type == "IDENT" and tokens[i].val == "hl" and tokens[i + 1] and tokens[i + 1].type == "DOT" and tokens[i + 2] and tokens[i + 2].type == "IDENT" and tokens[i + 2].val == "config") then return nil end
            if tokens[i + 3] and tokens[i + 3].type == "LPAREN" then return i + 4 end
            if tokens[i + 3] and tokens[i + 3].type == "LBRACE" then return i + 3 end
            return nil
        end

        local stack = {}
        local function in_function() for n = #stack, 1, -1 do if stack[n] == "function" then return true end end return false end
        local i = 1
        while i <= #tokens do
            local arg = nil
            if not in_function() then arg = config_arg_index(i) end
            if arg and tokens[arg] and tokens[arg].type == "LBRACE" then parse_table(arg, {}) end
            if tokens[i].type == "IDENT" then
                if tokens[i].val == "function" then stack[#stack + 1] = "function"
                elseif tokens[i].val == "end" then stack[#stack] = nil end
            end
            i = i + 1
        end

        if #matches == 0 then os.exit(1) end -- Exit 1 means pattern not found in THIS file.
        if #matches > 1 then os.exit(2) end  -- Ambiguous

        local m = matches[1]
        local ok, repl_or_err = pcall(format_replacement, m.raw)
        if not ok then die(3, repl_or_err) end
        
        local new_text = text:sub(1, m.s - 1) .. repl_or_err .. text:sub(m.e + 1)
        io.write(new_text)
        os.exit(0)
        """

        success = False
        try:
            # We iterate through all tracked files. If the mutator finds it, it outputs the new file.
            for src_file in self.loaded_files:
                if not Path(src_file).exists():
                    continue
                    
                res = subprocess.run([self.lua_bin, "-", src_file, target_key, target_scope, val_file_path],
                                     input=lua_mutator, text=True, capture_output=True, timeout=4)
                
                if res.returncode == 0 and res.stdout:
                    # Write stdout directly back to the matching modular file
                    with open(src_file, 'w') as f:
                        f.write(res.stdout)
                        
                    cache_key = f"{target_scope}/{target_key}" if target_scope else target_key
                    if new_value == "__DELETE__":
                        self.cache.pop(cache_key, None)
                    else:
                        self.cache[cache_key] = new_value
                    success = True
                    break  # Found and mutated successfully
                elif res.returncode == 1:
                    continue  # Key not in this specific file, check the next one
                else:
                    print(f"[Engine Warning] Mutator returned {res.returncode} for {src_file}. Error: {res.stderr}")
                    
        except Exception as e:
            print(f"[Engine Error] Exception during mutation: {e}")
            
        if os.path.exists(val_file_path):
            os.unlink(val_file_path)
            
        return success

# =============================================================================
# FOR DIOGNOSING ANY ISSUES, VERY IMPORTANT Commands!!
# =============================================================================
# python -m textual console
# python -m textual run --dev python_tui.py

# =============================================================================
# COLOR UTILITIES
# =============================================================================

KNOWN_COLORS = {
    "Red": (255, 0, 0), "Green": (0, 128, 0), "Lime": (0, 255, 0),
    "Blue": (0, 0, 255), "Yellow": (255, 255, 0), "Cyan": (0, 255, 255),
    "Magenta": (255, 0, 255), "White": (255, 255, 255), "Black": (0, 0, 0),
    "Gray": (128, 128, 128), "Silver": (192, 192, 192), "Maroon": (128, 0, 0),
    "Olive": (128, 128, 0), "Purple": (128, 0, 128), "Teal": (0, 128, 128),
    "Navy": (0, 0, 128), "Orange": (255, 165, 0), "Pink": (255, 192, 203),
    "Brown": (165, 42, 42), "Indigo": (75, 0, 130), "Violet": (238, 130, 238),
    "Gold": (255, 215, 0), "Coral": (255, 127, 80), "Salmon": (250, 128, 114),
    "Khaki": (240, 230, 140), "Plum": (221, 160, 221), "Turquoise": (64, 224, 208),
    "Crimson": (220, 20, 60), "Azure": (240, 255, 255), "Beige": (245, 245, 220),
    "Chocolate": (210, 105, 30), "Tomato": (255, 99, 71), "Lavender": (230, 230, 250)
}

CYCLE_COLORS = ["Red", "Lime", "Blue", "Yellow", "Cyan", "Magenta", "White", "Black"]

def parse_color_format(val: str) -> str:
    val = str(val).strip().lower()
    if val.startswith("0x"): return "0xhex"
    if val.startswith("#"): return "hex"
    if val.startswith("rgba"): return "rgba"
    if val.startswith("rgb"): return "rgb"
    if val.startswith("hsla"): return "hsla"
    if val.startswith("hsl"): return "hsl"
    if val.startswith("oklch"): return "oklch"
    return "hex"

def color_to_rgb(val: str) -> tuple[int, int, int]:
    """Gracefully extracts an RGB tuple to determine the color's display name and visual swatch."""
    val = str(val).strip().lower()
    if val.startswith("0x"):
        v = val[2:]
        if len(v) == 8: v = v[2:] 
        if len(v) >= 6:
            try: return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
            except ValueError: pass
    if val.startswith("#"):
        v = val[1:]
        if len(v) in (3, 4): 
            try: return (int(v[0]*2, 16), int(v[1]*2, 16), int(v[2]*2, 16))
            except ValueError: pass
        if len(v) >= 6: 
            try: return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
            except ValueError: pass
    
    m_rgb = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", val)
    if m_rgb:
        return (int(m_rgb.group(1)), int(m_rgb.group(2)), int(m_rgb.group(3)))
        
    m_hsl = re.match(r"hsla?\(\s*([\d.]+)\s*,\s*([\d.]+)%?\s*,\s*([\d.]+)%?", val)
    if m_hsl:
        h, s, l_ = float(m_hsl.group(1))/360.0, float(m_hsl.group(2))/100.0, float(m_hsl.group(3))/100.0
        r, g, b = colorsys.hls_to_rgb(h, l_, s)
        return (int(r*255), int(g*255), int(b*255))
        
    m_oklch = re.match(r"oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)", val)
    if m_oklch:
        # Approximate OKLCH mapping strictly for UI visualization purposes
        l_val, c_val, h_val = float(m_oklch.group(1)), float(m_oklch.group(2)), float(m_oklch.group(3))
        r, g, b = colorsys.hls_to_rgb(h_val/360.0, l_val, min(c_val*2.5, 1.0))
        return (max(0, min(255, int(r*255))), max(0, min(255, int(g*255))), max(0, min(255, int(b*255))))
        
    return (128, 128, 128)

def get_color_name(r: int, g: int, b: int) -> str:
    """Finds the nearest human readable color."""
    best_name = "Unknown"
    best_dist = float('inf')
    for name, color in KNOWN_COLORS.items():
        d = (r-color[0])**2 + (g-color[1])**2 + (b-color[2])**2
        if d < best_dist:
            best_dist = d
            best_name = name
    return best_name

def format_rgb(color_name: str, fmt: str, original_val: str) -> str:
    """Generates the appropriate string format based on the originally configured syntax."""
    r, g, b = KNOWN_COLORS.get(color_name, (128,128,128))
    
    if fmt == "hex":
        if len(original_val) == 9 and original_val.startswith("#"):
            return f"#{r:02x}{g:02x}{b:02x}{original_val[7:9]}"
        return f"#{r:02x}{g:02x}{b:02x}"
        
    if fmt == "0xhex":
        alpha = "ff"
        if original_val.startswith("0x") and len(original_val) == 10:
            alpha = original_val[2:4]
        return f"0x{alpha}{r:02x}{g:02x}{b:02x}"
        
    if fmt == "rgb":
        return f"rgb({r}, {g}, {b})"
        
    if fmt == "rgba":
        alpha = "1.0"
        m = re.search(r"rgba\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\)", original_val)
        if m: alpha = m.group(1)
        return f"rgba({r}, {g}, {b}, {alpha})"
        
    if fmt == "hsl" or fmt == "hsla":
        h, l, s = colorsys.rgb_to_hls(r/255.0, g/255.0, b/255.0)
        h_deg = int(h * 360)
        s_pct = int(s * 100)
        l_pct = int(l * 100)
        if fmt == "hsl":
            return f"hsl({h_deg}, {s_pct}%, {l_pct}%)"
        else:
            alpha = "1.0"
            m = re.search(r"hsla\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\)", original_val)
            if m: alpha = m.group(1)
            return f"hsla({h_deg}, {s_pct}%, {l_pct}%, {alpha})"
            
    if fmt == "oklch":
        oklch_map = {
            "Red": "oklch(0.628 0.258 29.23)",
            "Lime": "oklch(0.866 0.295 142.5)",
            "Blue": "oklch(0.452 0.313 264.05)",
            "Yellow": "oklch(0.968 0.211 109.77)",
            "Cyan": "oklch(0.905 0.183 195.58)",
            "Magenta": "oklch(0.702 0.322 328.36)",
            "White": "oklch(1.0 0 0)",
            "Black": "oklch(0.0 0 0)",
        }
        return oklch_map.get(color_name, "oklch(0.5 0.2 180)")
        
    return f"#{r:02x}{g:02x}{b:02x}"

# =============================================================================
# HOT-RELOADING NATIVE JSON THEME ENGINE
# =============================================================================

THEME_FILE_PATH = Path("~/.config/matugen/generated/dusky_tui.json").expanduser()

def load_matugen_json(file_path: Path) -> dict[str, str] | None:
    if not file_path.exists():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

THEME = {
    "bg": "#111318", "fg": "#e1e2e9", "accent": "#a8c8ff", 
    "error": "#ffb4ab", "warning": "#bdc7dc", "success": "#dbbce1", "muted": "#43474e"
}

_initial_theme = load_matugen_json(THEME_FILE_PATH)
if _initial_theme:
    THEME.update(_initial_theme)

# =============================================================================
# SCHEMA & DATA DEFINITIONS
# =============================================================================

type ConfigType = Literal["bool", "int", "float", "string", "cycle", "action", "menu", "picker", "color"]

@dataclass(kw_only=True)
class ConfigItem:
    label: str
    key: str
    scope: str = "DEFAULT"
    type_: ConfigType
    default: Any
    options: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    min_val: float | None = None
    max_val: float | None = None
    step: float | None = None
    value: Any = None

    def __post_init__(self) -> None:
        if self.value is None:
            self.value = self.default

TABS = ["General", "Network", "Display", "System", "Audio", "Storage", "Security"]
SCHEMA: dict[int, list[ConfigItem]] = {
    0: [
        ConfigItem(label="Enable Daemon", key="service_enabled", type_="bool", default=True),
        ConfigItem(label="Timeout (ms)", key="timeout", type_="int", default=100, min_val=0, max_val=5000, step=50),
        ConfigItem(label="Log Prefix", key="log_prefix", type_="string", default="myapp_"),
        ConfigItem(label="Scale Factor", key="scale", type_="float", default=1.0, min_val=0.5, max_val=3.0, step=0.1),
    ],
    1: [
        ConfigItem(label="Hostname", key="hostname", type_="string", default="arch-linux"),
        ConfigItem(label="Protocol", key="protocol", type_="cycle", default="tcp", options=["tcp", "udp", "icmp"]),
        ConfigItem(label="Port", key="port", type_="int", default=8080, min_val=1, max_val=65535, step=1),
    ],
    3: [
        ConfigItem(label="Select Theme", key="demo_picker", type_="picker", default="Tokyo Night", 
                   options=["Catppuccin Mocha", "Nord", "Dracula", "Gruvbox", "Tokyo Night"],
                   hints=["Warm & Pastel", "Arctic Cold", "Vampire Dark", "Retro Groove", "Neon Lights"]),
        ConfigItem(label="Restart Daemon", key="demo_sudo", type_="action", default=""),
        ConfigItem(label="Shadow Color (Hex)", key="color_hex", type_="color", default="0xee1a1a1a"),
        ConfigItem(label="Accent Color (RGB)", key="color_rgb", type_="color", default="rgb(0, 255, 0)"),
        ConfigItem(label="Highlight (OKLCH)", key="color_oklch", type_="color", default="oklch(0.628 0.258 29.23)"),
    ]
}

for i in range(len(TABS)):
    if i not in SCHEMA: SCHEMA[i] = []
    for j in range(len(SCHEMA[i]), 35):
        cat = TABS[i]
        cycle_type = j % 3
        if cycle_type == 0:
            SCHEMA[i].append(ConfigItem(label=f"{cat} Flag {j}", key=f"{cat.lower()}_{j}", type_="bool", default=(j % 2 == 0)))
        elif cycle_type == 1:
            SCHEMA[i].append(ConfigItem(label=f"{cat} Buffer {j}", key=f"{cat.lower()}_{j}", type_="int", default=256 + j, min_val=0, max_val=4096, step=16))
        else:
            SCHEMA[i].append(ConfigItem(label=f"{cat} Path {j}", key=f"{cat.lower()}_{j}", type_="string", default=f"/etc/{cat.lower()}/conf.d"))

# =============================================================================
# MODALS & OVERLAYS
# =============================================================================

class TextInputOverlay(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, default: str) -> None:
        super().__init__()
        self.prompt_text = prompt
        self.default_text = default

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            with Vertical(id="modal-content"):
                yield Label(self.prompt_text, id="modal-title")
                yield Input(value=self.default_text, id="modal-input")
                yield Label("Press Enter to save, Escape to cancel", id="modal-hint")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def handle_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)
        
    def action_cancel(self) -> None:
        self.dismiss(None)

class PickerScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("up,k", "cursor_up", "Up"),
        Binding("down,j", "cursor_down", "Down"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, title: str, options: list[str], hints: list[str]) -> None:
        super().__init__()
        self.picker_title = title
        self.options = options
        self.hints = hints

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            with Vertical(id="picker-content"):
                yield Label(f"PICKER: {self.picker_title}", id="picker-title")
                yield OptionList(id="picker-list")
                yield Label("Use ↑/↓ and Enter", id="modal-hint")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        for i, opt in enumerate(self.options):
            hint = self.hints[i] if i < len(self.hints) else ""
            txt = Text()
            txt.append(f" {opt} ", style="bold")
            if hint:
                txt.append(" - ")
                txt.append(hint, style=f"italic {THEME['muted']}")
            ol.add_option(Option(txt))
            
        ol.focus()

    @on(OptionList.OptionSelected)
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.options[event.option_index])

    def action_cursor_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_cancel(self) -> None:
        self.dismiss(None)

# =============================================================================
# INTERACTIVE COMPONENTS
# =============================================================================

class ConfigOptionList(OptionList):
    """Subclassed OptionList with native scroll tracking and cached index."""
    BINDINGS = [
        Binding("enter", "app.submit_current", "Action"),
        Binding("j,down", "cursor_down", "Down"),
        Binding("k,up", "cursor_up", "Up"),
        Binding("g", "scroll_top", "Top"),
        Binding("G", "scroll_bottom", "Bottom"),
        Binding("h,left,backspace", "app.adjust(-1)", "Adjust Down"),
        Binding("l,right", "app.adjust(1)", "Adjust Up"),
        Binding("r", "app.reset_item", "Reset"),
        Binding("R", "app.reset_all", "Reset Page"),
        Binding("ctrl+d,page_down", "page_down", "Page Down"),
        Binding("ctrl+u,page_up", "page_up", "Page Up"),
    ]
    
    last_highlighted_idx: int = 0
    _mouse_down_highlight: int | None = None
    _last_click_x: int = 0

    def action_scroll_top(self) -> None:
        self.highlighted = 0
        
    def action_scroll_bottom(self) -> None:
        if self.option_count > 0: self.highlighted = self.option_count - 1
        
    def action_page_down(self) -> None:
        if self.option_count == 0: return
        idx = self.highlighted if self.highlighted is not None else 0
        self.highlighted = min(self.option_count - 1, idx + 10)
        
    def action_page_up(self) -> None:
        if self.option_count == 0: return
        idx = self.highlighted if self.highlighted is not None else 0
        self.highlighted = max(0, idx - 10)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._mouse_down_highlight = self.highlighted
        self._last_click_x = event.x

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if hasattr(self.app, "_update_scroll_indicators"):
            self.app._update_scroll_indicators()
            
    def watch_max_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_max_scroll_y(old_value, new_value)
        if hasattr(self.app, "_update_scroll_indicators"):
            self.app._update_scroll_indicators()

    def on_resize(self, event: events.Resize) -> None:
        if hasattr(self.app, "_update_scroll_indicators"):
            self.app._update_scroll_indicators()

class ScrollIndicator(Label):
    _dragging: bool = False
    _max_scroll_y: float = 0
    _track_height: int = 0

    def update_scroll(self, scroll_y: float, max_scroll_y: float, viewport_height: float, virtual_height: float) -> None:
        if max_scroll_y <= 0 or virtual_height <= 0 or viewport_height <= 2:
            self.display = False
            return
        
        self.display = True
        self._max_scroll_y = max_scroll_y
        self._track_height = int(viewport_height) - 2
        
        if self._track_height < 1:
            self.update("▲\n▼")
            return
            
        thumb_size = max(1, int(self._track_height * (viewport_height / virtual_height)))
        max_pos = self._track_height - thumb_size
        
        if max_scroll_y > 0:
            pos = int((scroll_y / max_scroll_y) * max_pos)
        else:
            pos = 0
            
        txt = Text()
        txt.append("▲\n", style="bold")
        for i in range(self._track_height):
            if pos <= i < pos + thumb_size:
                txt.append("█\n")
            else:
                txt.append("│\n", style="dim")
        txt.append("▼", style="bold")
        self.update(txt)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if self._max_scroll_y <= 0: return
        try: tab_idx = int(self.id.split("-")[1])
        except (AttributeError, IndexError, ValueError): return
        
        ol = self.app.query_one(f"#list-{tab_idx}", ConfigOptionList)

        if event.y == 0:
            ol.scroll_y -= 1
        elif event.y == self.size.height - 1:
            ol.scroll_y += 1
        else:
            self._dragging = True
            self.capture_mouse()
            self._jump_to_y(event.y, ol)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._dragging:
            try: tab_idx = int(self.id.split("-")[1])
            except (AttributeError, IndexError, ValueError): return
            
            ol = self.app.query_one(f"#list-{tab_idx}", ConfigOptionList)
            self._jump_to_y(event.y, ol)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()

    def _jump_to_y(self, y: float, ol: ConfigOptionList) -> None:
        if self._track_height < 1: return
        relative_y = max(0, min(self._track_height - 1, y - 1))
        ratio = relative_y / (self._track_height - 1) if self._track_height > 1 else 0
        ol.scroll_y = int(ratio * self._max_scroll_y)

class Shortcut(Label):
    def __init__(self, key_text: str, label: str, action_name: str | None = None) -> None:
        super().__init__(classes="footer-shortcut")
        self.key_text = key_text
        self.label_text = label
        self.action_name = action_name

    def render(self) -> Text:
        txt = Text()
        txt.append(f"[{self.key_text}] ", style=THEME["accent"])
        txt.append(self.label_text, style=THEME["fg"])
        return txt

    def on_click(self) -> None:
        if self.action_name:
            getattr(self.app, f"action_{self.action_name}")()

class FileLink(Label):
    path = "~/.config/myapp/settings.conf"
    
    def render(self) -> Text:
        txt = Text()
        txt.append(" 󰈔 File: ", style=THEME["accent"])
        txt.append(self.path, style=THEME["fg"] + " underline")
        txt.append("  (Edit: LMB/RMB- GUI/Terminal)", style=f"italic {THEME['muted']}")
        return txt
        
    def on_click(self, event: events.Click) -> None:
        expanded_path = Path(self.path).expanduser().resolve()
        expanded_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            expanded_path.touch(exist_ok=True)
            if event.button == 1:
                subprocess.Popen(
                    ["xdg-open", str(expanded_path)], 
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            elif event.button == 3:
                editor = os.environ.get("VISUAL", os.environ.get("EDITOR", "nano"))
                with self.app.suspend():
                    subprocess.run([editor, str(expanded_path)])
        except (FileNotFoundError, OSError):
            if hasattr(self.app, "notify_status"):
                getattr(self.app, "notify_status")("Error resolving path or launching editor.")

class AppFooter(Vertical):
    status_msg = reactive("")

    def compose(self) -> ComposeResult:
        with Horizontal(id="footer-controls"):
            yield Shortcut("r", "Reset Item", "reset_item")
            yield Shortcut("R", "Reset Page", "reset_all")
            yield Shortcut("q", "Quit", "quit")
            yield Label(f"   [{THEME['error']}]●[/] Modified", id="footer-legend")
        
        with Horizontal(id="footer-bottom-row"):
            yield Label("", id="status-bar")
            yield FileLink(id="file-link")

    def watch_status_msg(self, new_val: str) -> None:
        for bar in self.query("#status-bar"):
            for link in self.query("#file-link"):
                if new_val:
                    txt = Text()
                    txt.append(" Status: ", style=THEME["accent"])
                    txt.append(new_val, style=THEME["error"])
                    bar.update(txt)
                    bar.display = True
                    link.display = False
                else:
                    bar.display = False
                    link.display = True

# =============================================================================
# MAIN APPLICATION
# =============================================================================

class DuskyApp(App):
    CSS = """
    Screen { background: $background; }
    
    #main-box {
        width: 100%; height: 100%;
        border: solid $primary 50%;
        border-title-color: $primary;
        border-title-style: bold;
        border-title-align: center;
        border-subtitle-color: $primary;
        border-subtitle-style: bold;
        border-subtitle-align: right;
        background: transparent;
        padding: 0 1;
    }
    
    TabbedContent { height: 1fr; margin-bottom: 1; background: transparent; }
    ContentSwitcher { height: 1fr; background: transparent; }
    
    Tabs { height: 1; margin-bottom: 1; background: transparent; }
    Tabs > .underline { display: none; }
    Tab { height: 1; padding: 0 1; color: $primary 60%; background: transparent; border: none; }
    Tab:hover { color: $text; background: $primary 25%; }
    Tab.-active { color: $background; background: $primary; text-style: bold; border: none; }
    
    .list-wrapper { height: 1fr; }
    ConfigOptionList { width: 1fr; height: 1fr; scrollbar-size: 0 0; background: transparent; border: none; }
    ConfigOptionList > .option-list--option { padding: 0 1; background: transparent; transition: background 150ms linear; }
    ConfigOptionList > .option-list--option-hover { background: $primary 10%; }
    ConfigOptionList > .option-list--option-highlighted { background: $primary 20%; }
    
    .indicator-column { width: 2; height: 1fr; background: transparent; align: right top; }
    ScrollIndicator { width: 1; height: 1fr; color: $primary; }
    ScrollIndicator:hover { color: $text; }
    
    #footer { height: 4; dock: bottom; border-top: solid $secondary; padding-top: 0; background: transparent; }
    #footer-controls { width: 100%; }
    
    .footer-shortcut { margin-right: 2; padding: 0 1; background: transparent; }
    .footer-shortcut:hover { text-style: bold; color: $text; background: $primary 25%; }
    #footer-legend { color: $text; padding-top: 0; }
    
    #footer-bottom-row { margin-top: 1; }
    #file-link { padding: 0 1; background: transparent; }
    #file-link:hover { text-style: bold; color: $text; background: $primary 25%; }
    
    /* MODAL STYLING WITH ROUNDED CORNERS - ZERO BLEED TRICK */
    TextInputOverlay, PickerScreen { align: center middle; background: rgba(0, 0, 0, 0.75); }
    
    #modal-dialog { width: 50; height: auto; background: transparent; border: round $primary; padding: 0; }
    #modal-content { width: 100%; height: auto; background: $background; padding: 1 2; }
    
    #picker-dialog { width: 60; height: 15; background: transparent; border: round $primary; padding: 0; }
    #picker-content { width: 100%; height: 100%; background: $background; padding: 1 2; }
    
    #modal-title, #picker-title { color: $primary; margin-bottom: 1; text-style: bold; border-bottom: solid $secondary; }
    #modal-hint { color: $secondary; text-style: italic; content-align: center middle; width: 100%; margin-top: 1; }
    
    Input { border: none; background: transparent; color: $text; border-bottom: solid $primary; }
    Input:focus { border: none; border-bottom: solid $primary; }
    
    #picker-list { height: 1fr; scrollbar-size: 0 0; background: transparent; border: none; }
    #picker-list > .option-list--option { padding: 0 1; background: transparent; transition: background 100ms linear; }
    #picker-list > .option-list--option-hover { background: $primary 10%; }
    #picker-list > .option-list--option-highlighted { background: $primary 20%; color: $text; text-style: bold; }
    """

    BINDINGS = [
        Binding("q,ctrl+c", "quit", "Quit", priority=True),
        Binding("tab", "next_tab", "Next Tab", priority=True),
        Binding("shift+tab", "prev_tab", "Prev Tab", priority=True),
        Binding("alt+1", "switch_tab(0)", "Tab 1", show=False),
        Binding("alt+2", "switch_tab(1)", "Tab 2", show=False),
        Binding("alt+3", "switch_tab(2)", "Tab 3", show=False),
        Binding("alt+4", "switch_tab(3)", "Tab 4", show=False),
        Binding("alt+5", "switch_tab(4)", "Tab 5", show=False),
        Binding("alt+6", "switch_tab(5)", "Tab 6", show=False),
        Binding("alt+7", "switch_tab(6)", "Tab 7", show=False),
    ]

    last_theme_mtime: float = 0.0
    _status_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="main-box"):
            with TabbedContent(id="tabs"):
                for i, name in enumerate(TABS):
                    with TabPane(name, id=f"tab-{i}"):
                        with Horizontal(classes="list-wrapper"):
                            yield ConfigOptionList(id=f"list-{i}")
                            with Vertical(classes="indicator-column"):
                                yield ScrollIndicator("", id=f"indicator-{i}")
            yield AppFooter(id="footer")

    def _build_option(self, item: ConfigItem, is_highlighted: bool = False) -> Text:
        """Constructs Rich text cleanly, mitigating arbitrary string injection bugs."""
        txt = Text()
        
        # Standardized purely geometric cursor to prevent bold font-fallback shifting
        CURSOR_CHAR = "▶"
        cursor = f"{CURSOR_CHAR} " if is_highlighted else "  "
        txt.append(cursor, style=f"{THEME['accent']} bold" if is_highlighted else "")
        
        label_style = f"{THEME['fg']} bold" if is_highlighted else THEME["fg"]
        txt.append(f"{item.label:<35}", style=label_style)
        
        val_str = str(item.value)
        def_str = str(item.default)
        
        if item.type_ == "action":
            # For actions, hide the dot entirely and use spacing to align properly
            txt.append("   ")
            txt.append("⚡ Execute Action", style=f"bold {THEME['warning']}")
        else:
            is_modified = val_str != def_str
            dot_color = THEME["error"] if is_modified else THEME["muted"]
            txt.append("●  ", style=dot_color)
            
            match item.type_:
                case "bool":
                    if item.value:
                        txt.append(" ◉ ON  ", style=f"bold {THEME['bg']} on {THEME['success']}")
                    else:
                        txt.append(" ◯ OFF ", style=f"{THEME['muted']} on {THEME['bg']}")
                case "string":
                    if val_str == "":
                        txt.append("[✎] Unset", style=f"italic {THEME['muted']}")
                    else:
                        txt.append(f"[✎] {val_str}", style=THEME["accent"])
                case "picker":
                    txt.append(f"[+] {val_str}", style=THEME["accent"])
                case "color":
                    r, g, b = color_to_rgb(val_str)
                    hex_color = f"#{r:02x}{g:02x}{b:02x}"
                    color_name = get_color_name(r, g, b)
                    txt.append(" ⬤ ", style=hex_color)
                    txt.append(f"{color_name}", style=THEME["accent"])
                case _:
                    txt.append(val_str, style=THEME["fg"])
                    
            if is_modified and is_highlighted:
                txt.append("   ↩ Reset", style=f"italic {THEME['error']}")
                
        return txt

    async def on_mount(self) -> None:
        self.query_one("#main-box").border_title = " Generic System Config Editor v7.0.4 "
        
        # Initialize Engine and load the actual disk state
        self.engine = HyprlandLuaEngine()
        loaded_state = self.engine.load_state()
        
        for i, items in SCHEMA.items():
            for item in items:
                cache_key = f"{item.scope}/{item.key}" if item.scope and item.scope != "DEFAULT" else item.key
                if cache_key in loaded_state:
                    raw_val = loaded_state[cache_key]
                    if item.type_ == "bool": item.value = str(raw_val).lower() == "true"
                    elif item.type_ == "int": item.value = int(float(raw_val))
                    elif item.type_ == "float": item.value = float(raw_val)
                    else: item.value = str(raw_val)

        self.apply_theme_to_engine()
        
        for i in range(len(TABS)):
            ol = self.query_one(f"#list-{i}", ConfigOptionList)
            items = SCHEMA.get(i, [])
            if items:
                options = [Option(self._build_option(item, is_highlighted=(idx == 0)), id=f"item_{i}_{idx}") for idx, item in enumerate(items)]
                ol.add_options(options)
                ol.last_highlighted_idx = 0

        if first_ol := self.current_option_list:
            first_ol.focus()
            self._update_pagination(first_ol)

        self.set_interval(0.5, self.watch_theme_file)
        self.call_after_refresh(self._update_scroll_indicators)

    @property
    def current_option_list(self) -> ConfigOptionList | None:
        try:
            tc = self.query_one(TabbedContent)
            if tc.active:
                idx = tc.active.split("-")[1]
                return self.query_one(f"#list-{idx}", ConfigOptionList)
        except Exception:
            pass
        return None

    def watch_theme_file(self) -> None:
        try:
            current_mtime = THEME_FILE_PATH.stat().st_mtime
            if current_mtime > self.last_theme_mtime:
                self.last_theme_mtime = current_mtime
                new_theme = load_matugen_json(THEME_FILE_PATH)
                
                if new_theme is not None:
                    THEME.update(new_theme) 
                    self.apply_theme_to_engine()
                    
                    for i in range(len(TABS)):
                        try:
                            ol = self.query_one(f"#list-{i}", ConfigOptionList)
                            items = SCHEMA.get(i, [])
                            last_idx = ol.last_highlighted_idx
                            
                            for idx, item in enumerate(items):
                                is_hl = (idx == last_idx) and (self.current_option_list == ol)
                                ol.replace_option_prompt_at_index(idx, self._build_option(item, is_hl))
                        except Exception:
                            continue
                            
                    for shortcut in self.query(Shortcut):
                        shortcut.refresh()
                        
                    for footer in self.query(AppFooter):
                        for legend in footer.query("#footer-legend"):
                            legend.update(f"   [{THEME['error']}]●[/] Modified")
                    for link in self.query(FileLink):
                        link.refresh()
        except OSError:
            pass

    def apply_theme_to_engine(self) -> None:
        # Ping-pong between two names to force Textual's reactive watcher to trigger 
        # a full CSS re-render without leaking memory.
        self._theme_toggle = not getattr(self, "_theme_toggle", False)
        theme_name = "dusky_matugen_A" if self._theme_toggle else "dusky_matugen_B"

        custom_theme = Theme(
            name=theme_name,
            primary=THEME["accent"],
            secondary=THEME["muted"],
            background=THEME["bg"],
            surface=THEME["bg"],
            warning=THEME["warning"],
            error=THEME["error"],
            success=THEME["success"],
            foreground=THEME["fg"],
        )
        
        self.register_theme(custom_theme)
        self.theme = theme_name

    @on(TabbedContent.TabActivated)
    def handle_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if ol := self.current_option_list:
            ol.focus()
            self._update_pagination(ol)
            self._update_scroll_indicators()

    @on(OptionList.OptionHighlighted)
    def handle_option_highlight(self, event: OptionList.OptionHighlighted) -> None:
        ol = event.option_list
        if not isinstance(ol, ConfigOptionList):
            return
            
        try:
            tab_idx = int(ol.id.split("-")[1])
        except (AttributeError, IndexError, ValueError):
            return
            
        last_idx = ol.last_highlighted_idx
        
        if last_idx is not None and last_idx != event.option_index:
            try:
                item = SCHEMA[tab_idx][last_idx]
                ol.replace_option_prompt_at_index(last_idx, self._build_option(item, False))
            except (IndexError, KeyError):
                pass
                
        if event.option_index is not None:
            try:
                item = SCHEMA[tab_idx][event.option_index]
                ol.replace_option_prompt_at_index(event.option_index, self._build_option(item, True))
                ol.last_highlighted_idx = event.option_index
            except (IndexError, KeyError):
                pass
            
        self._update_pagination(ol)

    def _update_pagination(self, ol: ConfigOptionList) -> None:
        idx = ol.highlighted if ol.highlighted is not None else 0
        total = ol.option_count
        main_box = self.query_one("#main-box")
        main_box.border_subtitle = f" {idx + 1}/{total} " if total else " 0/0 "

    def _update_scroll_indicators(self) -> None:
        tc = self.query_one(TabbedContent)
        if not tc.active: return
        
        try:
            tab_idx = int(tc.active.split("-")[1])
            ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
            indicator = self.query_one(f"#indicator-{tab_idx}", ScrollIndicator)
            
            if ol.max_scroll_y > 0 and ol.size.height > 2:
                indicator.update_scroll(
                    ol.scroll_y, 
                    ol.max_scroll_y, 
                    ol.size.height, 
                    ol.virtual_size.height
                )
            else:
                indicator.display = False
        except Exception:
            pass

    def notify_status(self, msg: str) -> None:
        app_footer = self.query_one(AppFooter)
        app_footer.status_msg = msg
        
        if self._status_timer is not None:
            self._status_timer.stop()
            
        self._status_timer = self.set_timer(3, lambda: setattr(app_footer, 'status_msg', ""))

    def action_next_tab(self) -> None: 
        self.query_one(Tabs).action_next_tab()
        
    def action_prev_tab(self) -> None: 
        self.query_one(Tabs).action_previous_tab()
        
    def action_switch_tab(self, index: int) -> None:
        if 0 <= index < len(TABS):
            tc = self.query_one(TabbedContent)
            tc.active = f"tab-{index}"

    def _sync_item(self, item: ConfigItem, new_val: Any, ol: ConfigOptionList, item_idx: int) -> None:
        """Helper to write configuration before updating the UI state."""
        success = self.engine.write_value(item.key, item.scope, new_val)
        if success:
            item.value = new_val
            is_hl = (item_idx == ol.highlighted)
            ol.replace_option_prompt_at_index(item_idx, self._build_option(item, is_hl))
            self.notify_status(f"Written: {item.label} = {new_val}")
            
            # Hot reload trigger
            if item.type_ == "action" and item.key == "reload":
                subprocess.run(["hyprctl", "reload"], capture_output=True)
                self.notify_status("Hyprland Daemon Reloaded.")
        else:
            self.notify_status(f"Error: Failed to write {item.label} to file.")

    def action_adjust(self, direction: int) -> None:
        ol = self.current_option_list
        if not ol or ol.highlighted is None: return
        
        tc = self.query_one(TabbedContent)
        tab_idx = int(tc.active.split("-")[1])
        item_idx = ol.highlighted
        item = SCHEMA.get(tab_idx, [])[item_idx]
        
        new_val = item.value
        match item.type_:
            case "bool":
                new_val = not item.value
            case "int" | "float":
                step = item.step or 1
                new_val = item.value + (direction * step)
                if item.min_val is not None: new_val = max(item.min_val, new_val)
                if item.max_val is not None: new_val = min(item.max_val, new_val)
                new_val = round(new_val, 6) if item.type_ == "float" else int(new_val)
            case "cycle":
                if not item.options: return
                try: idx = item.options.index(item.value)
                except ValueError: idx = 0
                new_val = item.options[(idx + direction) % len(item.options)]
            case "color":
                r, g, b = color_to_rgb(str(item.value))
                current_name = get_color_name(r, g, b)
                try: idx = CYCLE_COLORS.index(current_name)
                except ValueError: idx = 0
                next_name = CYCLE_COLORS[(idx + direction) % len(CYCLE_COLORS)]
                fmt = parse_color_format(str(item.value))
                new_val = format_rgb(next_name, fmt, str(item.value))
            case _: return
            
        self._sync_item(item, new_val, ol, item_idx)

    def action_reset_item(self) -> None:
        ol = self.current_option_list
        if ol and ol.highlighted is not None:
            tc = self.query_one(TabbedContent)
            tab_idx = int(tc.active.split("-")[1])
            item_idx = ol.highlighted
            item = SCHEMA[tab_idx][item_idx]
            
            self._sync_item(item, item.default, ol, item_idx)

    def action_reset_all(self) -> None:
        tc = self.query_one(TabbedContent)
        if not tc.active: return
        
        tab_idx = int(tc.active.split("-")[1])
        items = SCHEMA.get(tab_idx, [])
            
        if ol := self.current_option_list:
            for idx, item in enumerate(items):
                self._sync_item(item, item.default, ol, idx)
                
        self.notify_status(f"Reset all items in {TABS[tab_idx]}")

    def action_submit_current(self) -> None:
        ol = self.current_option_list
        if ol and ol.highlighted is not None:
            # Ensure keyboard submits don't use stale mouse coordinates
            ol._last_click_x = 0
            ol._mouse_down_highlight = None
            self._handle_item_action(ol, ol.highlighted)

    @on(OptionList.OptionSelected)
    def handle_selection(self, event: OptionList.OptionSelected) -> None:
        ol = event.option_list
        if isinstance(ol, ConfigOptionList):
            # Only trigger action if item was already highlighted prior to the click
            if getattr(ol, "_mouse_down_highlight", None) == event.option_index:
                self._handle_item_action(ol, event.option_index)
            # Reset mouse tracking to prevent keyboard hijacks
            ol._mouse_down_highlight = None
            ol._last_click_x = 0

    def _handle_item_action(self, ol: ConfigOptionList, index: int) -> None:
        try:
            tab_idx = int(ol.id.split("-")[1])
            item = SCHEMA[tab_idx][index]
        except (AttributeError, IndexError, ValueError, KeyError):
            return
            
        is_modified = str(item.value) != str(item.default)
        
        # Smart detection for clicking the "Reset" string on the right margin
        if is_modified and item.type_ != "action":
            val_str = str(item.value)
            if item.type_ == "bool": 
                threshold = 47
            elif item.type_ in ("string", "picker"): 
                threshold = 44 + len(val_str)
            elif item.type_ == "color":
                r, g, b = color_to_rgb(val_str)
                c_name = get_color_name(r, g, b)
                threshold = 40 + len(c_name) + 3
            else: 
                threshold = 40 + len(val_str)
            
            click_x = getattr(ol, "_last_click_x", 0)
            if threshold <= click_x <= threshold + 12: # Safe bound
                self._sync_item(item, item.default, ol, index)
                return
                
        match item.type_:
            case "bool" | "cycle": 
                self.action_adjust(1)
            case "int" | "float" | "string" | "color": 
                self.prompt_string(ol, tab_idx, index, item)
            case "action":
                if item.key == "demo_sudo":
                    self.notify_status("Acquiring Sudo... Simulated daemon restart.")
                else:
                    self.notify_status(f"Executed: {item.label}")
            case "picker": 
                self.prompt_picker(ol, tab_idx, index, item)

    def prompt_string(self, ol: ConfigOptionList, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        def check_reply(new_val: str | None) -> None:
            if new_val is not None:
                if item.type_ == "int":
                    try: new_val = int(new_val)
                    except ValueError: 
                        self.notify_status("Error: Value must be an integer.")
                        return
                elif item.type_ == "float":
                    try: new_val = float(new_val)
                    except ValueError: 
                        self.notify_status("Error: Value must be a float.")
                        return
                        
                self._sync_item(item, new_val, ol, item_idx)
                
        self.push_screen(TextInputOverlay(f"Enter new {item.label}:", str(item.value)), check_reply)

    def prompt_picker(self, ol: ConfigOptionList, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        def check_reply(new_val: str | None) -> None:
            if new_val is not None:
                self._sync_item(item, new_val, ol, item_idx)
                
        self.push_screen(PickerScreen(item.label, item.options, item.hints), check_reply)


if __name__ == "__main__":
    app = DuskyApp()
    app.run()
