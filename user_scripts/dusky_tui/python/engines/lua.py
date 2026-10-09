#!/usr/bin/env python3
import os
import re
lazy import math
lazy import threading
lazy import stat
lazy import json
lazy import subprocess
lazy import tempfile
lazy import shutil
lazy from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine
from python.shared.config_io import current_stamp, stamp

# =============================================================================
# [ BLOCK 1: THE ENGINE ]
# Targets Python 3.15 and the installed Lua interpreter.
# Unified Pathlib usage, refined subprocess handling, and modernized typing.
# =============================================================================

class HyprlandLuaEngine(BaseEngine):
    def __init__(self, config_path: str = "~/Documents/hyprland.lua"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.config_dir = self.config_path.parent
        hypr_root = (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "hypr").expanduser().resolve()
        self.module_root = hypr_root if self.config_path.is_relative_to(hypr_root) else self.config_dir
        self.lua_bin = self._find_lua()
        self.cache: dict[str, Any] = {}
        self.loaded_files: list[str] = []
        self.file_mtimes: dict[str, float] = {}
        self._file_stamps = {}
        self._call_sites = []
        self._lock = threading.RLock()

    @property
    def target_path(self) -> str:
        """Fulfills BaseEngine contract to supply the UI with the file path."""
        return str(self.config_path)

    def _find_lua(self) -> str:
        # Arch Linux natively uses 'lua' for the latest version.
        for cmd in ("lua", "lua5.4", "lua54"):
            cmd_path = shutil.which(cmd)
            if cmd_path:
                try:
                    subprocess.run(
                        [cmd_path, "-E", "-e", "local major, minor = _VERSION:match('Lua (%d+)%.(%d+)'); assert(tonumber(major) > 5 or (tonumber(major) == 5 and tonumber(minor) >= 4))"],
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        check=True,
                        timeout=5
                    )
                    return cmd_path
                except (subprocess.SubprocessError, OSError):
                    continue
        raise RuntimeError("Lua 5.4+ not found in system PATH.")

    def _is_safe_path(self, target_path: str) -> bool:
        """Jail constraint: Only allow .lua files within the config directory hierarchy."""
        try:
            resolved = Path(target_path).resolve()
            return resolved.suffix == '.lua' and self.config_dir in resolved.parents
        except (OSError, RuntimeError):
            return False

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            try:
                return self._load_state()
            except (OSError, UnicodeError) as exc:
                self.cache = {}
                self.loaded_files = []
                self.file_mtimes = {}
                self._file_stamps = {}
                self._call_sites = []
                print(f'Failed to read Lua configuration: {exc}')
                return self.cache

    def _load_state(self) -> dict[str, Any]:
        self.cache = {}
        self.loaded_files = []
        self.file_mtimes = {}
        self._file_stamps = {}
        self._call_sites = []
        if not self.config_path.exists(): 
            return {}

        main_before = self.config_path.stat()
        self.file_mtimes[str(self.config_path)] = main_before.st_mtime

        # THE HYPRLAND v0.55.0+ DYNAMIC SANDBOX
        lua_evaluator = r"""
        local main_path = arg[1]
        local config_dir = arg[2]
        local module_root = arg[3]
        local config_root = {}
        local loaded_files = {main_path}
        local calls = {}
        local function record_call(method, id, info)
            if info and info.source:sub(1, 1) == "@" then
                calls[#calls + 1] = {file = info.source:sub(2), line = info.currentline,
                                    method = method, id = tostring(id)}
            end
        end
        
        local function deep_merge(dst, src) 
            for k, v in pairs(src) do 
                if type(v) == "table" then 
                    if type(dst[k]) ~= "table" then dst[k] = {} end 
                    deep_merge(dst[k], v) 
                else dst[k] = v end 
            end 
            return dst 
        end
        
        local function append_list(list_name, tbl)
            if type(tbl) == "table" then
                if not config_root[list_name] then config_root[list_name] = {} end
                table.insert(config_root[list_name], tbl)
                return tbl.name or tbl.output or tbl.workspace or #config_root[list_name]
            end
        end
        
        local inert_proxy
        local proxy_mt = {
            __index = function() return inert_proxy end,
            __newindex = function() end,
            __call = function() return inert_proxy end,
            __tostring = function() return "" end,
            __concat = function() return "" end,
            __len = function() return 0 end,
        }
        inert_proxy = setmetatable({}, proxy_mt)
        
        -- DYNAMIC ENDPOINT INTERCEPTION WITH BIND AWARENESS
        local hl = setmetatable({}, {
            __index = function(_, key)
                if key == "config" then
                    return function(tbl)
                        if type(tbl) == "table" then
                            deep_merge(config_root, tbl)
                            record_call(key, "", debug.getinfo(2, "Sl"))
                        end
                    end
                elseif key == "bind" or key == "unbind" then
                    return function(bind_key, dispatcher, flags)
                        local entry = flags
                        if type(entry) ~= "table" then entry = {} end
                        record_call(key, bind_key, debug.getinfo(2, "Sl"))
                        entry._bind_key = bind_key
                        if not config_root[key] then config_root[key] = {} end
                        table.insert(config_root[key], entry)
                    end
                elseif key == "env" then
                    return function(env_key, env_val)
                        if type(env_key) == "string" then
                            if not config_root["env"] then config_root["env"] = {} end
                            table.insert(config_root["env"], { key = env_key, value = env_val })
                            record_call(key, #config_root["env"], debug.getinfo(2, "Sl"))
                        end
                    end
                elseif key == "layout" then
                    return inert_proxy
                else
                    return function(tbl)
                        local id = append_list(key, tbl)
                        if id then record_call(key, id, debug.getinfo(2, "Sl")) end
                    end
                end
            end
        })

        local safe_env = { 
            hl = hl, math = math, string = string, table = table, type = type, 
            pairs = pairs, ipairs = ipairs, tostring = tostring, tonumber = tonumber, 
            HOME = os.getenv("HOME") or "",
            os = {getenv = os.getenv},
            assert = assert, error = error, pcall = pcall, select = select,
            next = next, setmetatable = setmetatable, getmetatable = getmetatable,
            io = {
                open = function(path, mode)
                    if mode and mode:match("w") then return nil end
                    -- Sandbox strict whitelist: path must reside in config_dir and have no upward traversal
                    if path:match("%.%.") then return nil end
                    local safe_dir = config_dir:gsub("([%-%.%+%[%]%(%)%$%^%%%?%*])", "%%%1")
                    if safe_dir:sub(-1) ~= "/" then safe_dir = safe_dir .. "/" end
                    if path ~= config_dir and not path:match("^" .. safe_dir) then return nil end
                    return io.open(path, "r")
                end
            }, 
            print = function(...) 
                local args = {...}
                for i, v in ipairs(args) do io.stderr:write(tostring(v) .. "\t") end
                io.stderr:write("\n")
            end 
        }
        
        -- SURGICAL FIX: Intercept undefined globals to preserve them as variable references rather than throwing nils
        setmetatable(safe_env, {
            __index = function(_, key)
                if type(key) == "string" and key:match("^[A-Za-z_][A-Za-z0-9_]*$") then
                    return "__VAR__" .. key
                end
                return nil
            end
        })
        safe_env._G = safe_env
        
        safe_env.dofile = function(path) 
            if not path:match("%.lua$") then return nil end
            if path:sub(1, 1) ~= "/" then path = config_dir .. "/" .. path end
            table.insert(loaded_files, path)
            local chunk, err = loadfile(path, "t", safe_env)
            if not chunk then error(err) end
            return chunk()
        end
        
        local required_modules = {}
        safe_env.require = function(path)
            if required_modules[path] then return required_modules[path] end
            local relative = path:gsub("%.", "/") .. ".lua"
            local resolved = config_dir .. "/" .. relative
            local local_file = io.open(resolved, "rb")
            if local_file then
                local_file:close()
            else
                resolved = module_root .. "/" .. relative
            end
            local result = safe_env.dofile(resolved)
            if result == nil then result = true end
            required_modules[path] = result
            return result
        end
        
        local chunk, err = loadfile(main_path, "t", safe_env)
        if not chunk then error(err) end
        chunk()
        
        local out_state = {}
        local seen_keys = {}
        local function escape_str(s) 
            s = s:gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n'):gsub('\r', '\\r'):gsub('\t', '\\t')
            s = s:gsub('[%c]', function(c) return string.format('\\u%04x', string.byte(c)) end)
            return '"' .. s .. '"' 
        end

        -- SECURE WALK FUNCTION: Preserves data types for Python JSON loader
        local function walk(t, scope, seen, is_record)
            seen = seen or {}
            if seen[t] then return end
            seen[t] = true
            
            for k, v in pairs(t) do 
                if type(k) == "string" or type(k) == "number" then 
                    local str_k = tostring(k)
                    local is_ident_key = false
                    
                    if type(v) == "table" and type(k) == "number" then
                        local id = v.name or v.output or v.workspace or v._bind_key
                        if id then str_k = tostring(id) end
                    end

                    if is_record and type(k) == "string" and (k == "name" or k == "output" or k == "workspace" or k == "_bind_key") then
                        is_ident_key = true
                    end

                    if not is_ident_key then
                        local new_scope = scope == "" and str_k or (scope .. "/" .. str_k)
                        if type(v) == "table" then 
                            walk(v, new_scope, seen, type(k) == "number")
                        else 
                            seen_keys[new_scope] = true
                            local val_str
                            if type(v) == "string" then val_str = escape_str(v)
                            elseif type(v) == "boolean" then val_str = tostring(v)
                            elseif type(v) == "number" then
                                if v ~= v or v == math.huge or v == -math.huge then val_str = escape_str(tostring(v))
                                else val_str = math.type(v) == "integer" and tostring(v) or string.format("%.17g", v) end
                            else val_str = escape_str(tostring(v)) end
                            table.insert(out_state, escape_str(new_scope)..":"..val_str) 
                        end 
                    end
                end 
            end 
            seen[t] = nil
        end
        walk(config_root, "")

        -- SCANNER FOR EXPLICIT NIL ASSIGNMENTS (Lua runtime omits nil keys from tables)
        local function tokenize(text)
            local len = #text
            local tokens = {}
            local pos = 1
            local function is_alpha(c) return c:match("^[A-Za-z_]$") ~= nil end
            local function is_alnum(c) return c:match("^[A-Za-z0-9_]$") ~= nil end
            local function is_space(c) return c == " " or c == "\t" or c == "\r" or c == "\n" or c == "\v" or c == "\f" end
            local function add(tp, val, s, e) tokens[#tokens + 1] = { type = tp, val = val, s = s, e = e } end

            local function long_bracket_end_at(p)
                if text:sub(p, p) ~= "[" then return nil end
                local q = p + 1
                while q <= len and text:sub(q, q) == "=" do q = q + 1 end
                if text:sub(q, q) ~= "[" then return nil end
                local eqs = text:sub(p + 1, q - 1)
                local close = "]" .. eqs .. "]"
                local found = text:find(close, q + 1, true)
                return found and (found + #close - 1) or nil
            end

            while pos <= len do
                local c = text:sub(pos, pos)
                if is_space(c) then pos = pos + 1
                elseif c == "-" and text:sub(pos + 1, pos + 1) == "-" then
                    pos = pos + 2
                    local lb_end = long_bracket_end_at(pos)
                    if lb_end then pos = lb_end + 1
                    else
                        local nl = text:find("\n", pos, true)
                        if nl then pos = nl + 1 else pos = len + 1 end
                    end
                elseif c == "'" or c == '"' then
                    local quote = c; local s = pos; pos = pos + 1
                    while pos <= len do
                        local ch = text:sub(pos, pos)
                        if ch == "\\" then pos = pos + 2
                        elseif ch == quote then pos = pos + 1; break
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
                    while pos <= len do
                        local nc = text:sub(pos, pos)
                        if nc:match("^[A-Za-z0-9_%.]$") then pos = pos + 1
                        elseif (nc == "+" or nc == "-") and text:sub(pos - 1, pos - 1):match("^[eEpP]$") then pos = pos + 1
                        else break end
                    end
                    add("NUMBER", text:sub(s, pos - 1), s, pos - 1)
                else
                    local map = { ["{"]="LBRACE", ["}"]="RBRACE", ["("]="LPAREN", [")"]="RPAREN", ["["]="LBRACK", ["]"]="RBRACK", ["="]="EQUALS", [","]="COMMA", [";"]="SEMI", ["."]="DOT", [":"]="COLON" }
                    add(map[c] or "OTHER", c, pos, pos); pos = pos + 1
                end
            end
            return tokens
        end

        local function key_at(tokens, i)
            local tok = tokens[i]
            if not tok then return nil, i end
            if tok.type == "IDENT" and tokens[i + 1] and tokens[i + 1].type == "EQUALS" then 
                return tok.val, i + 2 
            end
            if tok.type == "LBRACK" and tokens[i + 1] and tokens[i + 1].type == "STRING" and tokens[i + 2] and tokens[i + 2].type == "RBRACK" and tokens[i + 3] and tokens[i + 3].type == "EQUALS" then
                local str_val = tokens[i + 1].val
                local decoder = assert(load("return " .. str_val, "key", "t", {}))
                return decoder(), i + 4
            end
            return nil, i
        end

        local function find_rhs_end(tokens, i)
            local j = i; local depth = 0; local block_depth = 0; local rhs_end = i
            while j <= #tokens do
                local tp = tokens[j].type; local val = tokens[j].val
                local prev_tp = j > 1 and tokens[j-1].type or nil
                if tp == "IDENT" and prev_tp ~= "DOT" then
                    if (val == "function" or val == "if" or val == "do" or val == "repeat") then block_depth = block_depth + 1
                    elseif (val == "end" or val == "until") and block_depth > 0 then block_depth = block_depth - 1 end
                end
                if block_depth == 0 then
                    if tp == "LBRACE" or tp == "LPAREN" or tp == "LBRACK" then depth = depth + 1
                    elseif tp == "RBRACE" or tp == "RPAREN" or tp == "RBRACK" then 
                        if depth == 0 then break end
                        depth = depth - 1 
                    elseif depth == 0 and (tp == "COMMA" or tp == "SEMI") then break end
                end
                rhs_end = j; j = j + 1
            end
            return rhs_end, j
        end

        local function scan_table(tokens, text, i, scope_parts)
            if not tokens[i] or tokens[i].type ~= "LBRACE" then return i end
            i = i + 1
            while i <= #tokens do
                if tokens[i].type == "RBRACE" then return i + 1 end
                if tokens[i].type == "COMMA" or tokens[i].type == "SEMI" then 
                    i = i + 1
                else
                    local key, rhs = key_at(tokens, i)
                    if key then
                        if tokens[rhs] and tokens[rhs].type == "LBRACE" then
                            scope_parts[#scope_parts + 1] = key
                            local next_i = scan_table(tokens, text, rhs, scope_parts)
                            scope_parts[#scope_parts] = nil
                            i = next_i
                        else
                            local rhs_end, next_i = find_rhs_end(tokens, rhs)
                            local curr_scope = table.concat(scope_parts, "/")
                            local raw = text:sub(tokens[rhs].s, tokens[rhs_end].e):gsub("^%s+", ""):gsub("%s+$", "")
                            if raw == "nil" then
                                local full_k = curr_scope == "" and key or (curr_scope .. "/" .. key)
                                if not seen_keys[full_k] then
                                    seen_keys[full_k] = true
                                    table.insert(out_state, escape_str(full_k) .. ':"nil"')
                                end
                            end
                            i = next_i
                        end
                    else
                        local rhs_end, next_i = find_rhs_end(tokens, i)
                        i = next_i
                    end
                end
            end
            return i
        end

        local active_config_lines = {}
        for _, call in ipairs(calls) do
            if call.method == "config" then
                active_config_lines[call.file] = active_config_lines[call.file] or {}
                active_config_lines[call.file][call.line] = true
            end
        end
        for _, fpath in ipairs(loaded_files) do
            local f = io.open(fpath, "rb")
            if f then
                local text = f:read("*a"); f:close()
                local tokens = tokenize(text)
                local line, cursor = 1, 1
                local source_counts = {}
                for i = 1, #tokens do
                    while cursor < tokens[i].s do
                        local next_line = text:find("\n", cursor, true)
                        if not next_line or next_line >= tokens[i].s then cursor = tokens[i].s; break end
                        line, cursor = line + 1, next_line + 1
                    end
                    tokens[i].line = line
                    if tokens[i].val == "hl" and tokens[i+1] and tokens[i+1].type == "DOT"
                       and tokens[i+2] and tokens[i+2].val == "config" then
                        source_counts[line] = (source_counts[line] or 0) + 1
                    end
                end
                for i = 1, #tokens - 4 do
                    local line = tokens[i].line
                    local executed = active_config_lines[fpath] and active_config_lines[fpath][line]
                    if executed and source_counts[line] == 1 and tokens[i].val == "hl" and tokens[i+1].type == "DOT" and tokens[i+2].val == "config" then
                        if tokens[i+3].type == "LPAREN" and tokens[i+4].type == "LBRACE" then
                            scan_table(tokens, text, i+4, {})
                        elseif tokens[i+3].type == "LBRACE" then
                            scan_table(tokens, text, i+3, {})
                        end
                    end
                end
            end
        end
        
        local out_files = {}
        for _, f in ipairs(loaded_files) do table.insert(out_files, escape_str(f)) end
        
        local out_calls = {}
        for _, call in ipairs(calls) do
            out_calls[#out_calls + 1] = '{"file":' .. escape_str(call.file) .. ',"line":' .. call.line
                .. ',"method":' .. escape_str(call.method) .. ',"id":' .. escape_str(call.id) .. '}'
        end
        io.stdout:write('{"state": {' .. table.concat(out_state, ",") .. '}, "files": ['
            .. table.concat(out_files, ",") .. '], "calls": [' .. table.concat(out_calls, ",") .. ']}')
        """
        
        try:
            res = subprocess.run(
                [self.lua_bin, "-E", "-", str(self.config_path), str(self.config_dir), str(self.module_root)],
                input=lua_evaluator, 
                text=True, 
                encoding='utf-8', 
                capture_output=True, 
                timeout=5.0,
                cwd=self.config_dir
            )
            
            if res.returncode == 0 and res.stdout.strip():
                if current_stamp(self.config_path) != stamp(main_before):
                    raise OSError('Main Lua configuration changed during evaluation. Reload required.')
                data = json.loads(res.stdout)
                self.cache = data.get("state", {})
                self._call_sites = data.get("calls", [])
                for call in self._call_sites:
                    call["file"] = str(Path(call["file"]).resolve())
                
                raw_files = data.get("files", [str(self.config_path)])
                self.loaded_files = list(dict.fromkeys(str(Path(f).resolve()) for f in raw_files if self._is_safe_path(f)))
                
                for f in self.loaded_files:
                    path_obj = Path(f)
                    if path_obj.exists():
                        info = path_obj.stat()
                        self.file_mtimes[f] = info.st_mtime
                        self._file_stamps[f] = (info.st_mtime, stamp(info))
                        
                return self.cache
            else:
                print(f"Load Error (Return Code {res.returncode}): {res.stderr}")
        except (json.JSONDecodeError, UnicodeError) as e:
            print(f"Failed to parse Lua state JSON: {e}")
        except subprocess.TimeoutExpired:
            print("Load Exception: Lua evaluation timed out.")
        except (OSError, subprocess.SubprocessError) as e: 
            print(f"Load Exception: {e}")
            
        self.cache = {}
        self.loaded_files = []
        self.file_mtimes = {}
        return self.cache

    @staticmethod
    def _lua_string(value: str) -> str:
        # Lua uses decimal byte escapes; JSON's \uXXXX syntax is not Lua syntax.
        escaped = []
        for char in value:
            if char in {'"', '\\'}:
                escaped.append('\\' + char)
            elif ord(char) < 32 or ord(char) == 127:
                escaped.append(f'\\{ord(char):03d}')
            else:
                escaped.append(char)
        return '"' + ''.join(escaped) + '"'
    
    def _is_raw_lua_val(self, val: str) -> bool:
        if val in {"true", "false", "nil", "__DELETE__"}: 
            return True
        
        return re.fullmatch(r'[+-]?(?:0[xX][0-9a-fA-F]+(?:\.[0-9a-fA-F]*)?(?:[pP][+-]?[0-9]+)?|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)', val) is not None

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        """
        Proxy method. Routes single mutations through the unified high-speed batch architecture.
        Guarantees zero behavioral drift between single UI edits and bulk UI presets.
        """
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        with self._lock:
            try:
                return self._write_batch(changes)
            except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as exc:
                return False, f'Lua write failed: {exc}', ''

    def _write_batch(self, changes):
        """Stage and validate all files before replacing any; renames are per-file."""
        if not changes:
            return True, "No pending changes.", ""
            
        if not self.loaded_files:
            self.load_state()
            if not self.loaded_files:
                return False, "Cannot evaluate Lua configuration. Reload required.", ""

        # Concurrency safety check
        refresh_calls = False
        for src_file in self.loaded_files:
            target_path = Path(src_file)
            if target_path.exists():
                cached_mtime = self.file_mtimes.get(src_file)
                if cached_mtime is not None and target_path.stat().st_mtime != cached_mtime:
                    return False, f"File {src_file} modified externally. Reload required.", ""
                remembered = self._file_stamps.get(src_file)
                # Derived engines maintain file_mtimes after their own source edits.
                if remembered and remembered[0] != cached_mtime:
                    refresh_calls = True
                if remembered and remembered[0] == cached_mtime and current_stamp(target_path) != remembered[1]:
                    return False, f"File {src_file} modified externally. Reload required.", ""
            elif src_file in self.file_mtimes:
                return False, f'File {src_file} was removed externally. Reload required.', ''

        if refresh_calls:
            self.load_state()
            if not self.loaded_files:
                return False, "Cannot refresh edited Lua configuration. Reload required.", ""

        # Last change wins for repeated UI bindings, without inflated accounting.
        changes = list({(key, scope): (key, scope, value, kind) for key, scope, value, kind in changes}.values())
        payload = ['return {']
        for key, scope, new_value, item_type in changes:
            if new_value is None or new_value in ('nil', '__DELETE__'):
                value = 'nil'
            elif isinstance(new_value, str) and new_value.startswith('__VAR__'):
                value = new_value[7:]
            elif item_type == 'bool':
                value = 'true' if str(new_value).strip().lower() in {'true', '1', 'yes', 'on', 't', 'y'} else 'false'
            elif item_type in ('int', 'float'):
                value = str(new_value)
                if not self._is_raw_lua_val(value) or not math.isfinite(float.fromhex(value) if '0x' in value.lower() else float(value)):
                    raise ValueError(f'Invalid Lua {item_type}: {value!r}')
            elif item_type != 'string' and self._is_raw_lua_val(str(new_value)):
                value = str(new_value)
            else:
                value = self._lua_string(str(new_value))
            payload.append(f'  {{ key = {self._lua_string(key)}, scope = {self._lua_string(scope)}, val = {self._lua_string(value)} }},')
        payload.append('  calls = {')
        for call in self._call_sites:
            payload.append('    {file = ' + self._lua_string(call['file'])
                           + ', line = ' + str(call['line'])
                           + ', method = ' + self._lua_string(call['method'])
                           + ', id = ' + self._lua_string(call['id']) + '},')
        lua_table = '\n'.join([*payload, '  }', '}'])
        snapshots = {file: Path(file).stat() for file in self.loaded_files}

        status_msg = "Failed"
        debug_output = ""
        success = False
        
        committed_count = 0
        pending_replacements: list[tuple[Path, Path, str]] = []
        temp_files_created: list[Path] = []
        successful_commits: set[tuple[str, str]] = set()
        batch_path = None

        try:
            # Write the batch payload to disk exactly once
            with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', suffix=".lua") as vf:
                batch_path = Path(vf.name)
                vf.write(lua_table)

            lua_mutator = r"""
            local src_path = assert(arg[1], "missing source")
            local batch_path = assert(arg[2], "missing batch file")
            local out_path = assert(arg[3], "missing out file")

            local function read_file(path)
                local f = io.open(path, "rb")
                if not f then os.exit(4) end
                local s = f:read("*a"); f:close()
                return s
            end

            -- Load the O(1) batched instructions
            local batch = dofile(batch_path)
            local batch_lookup = {}
            for _, item in ipairs(batch) do
                batch_lookup[item.scope .. "\0" .. item.key] = item.val
            end

            local function tokenize(text)
                local len = #text
                local tokens = {}
                local pos = 1

                local function is_alpha(c) return c:match("^[A-Za-z_]$") ~= nil end
                local function is_alnum(c) return c:match("^[A-Za-z0-9_]$") ~= nil end
                local function is_space(c) return c == " " or c == "\t" or c == "\r" or c == "\n" or c == "\v" or c == "\f" end
                local function add(tp, val, s, e) tokens[#tokens + 1] = { type = tp, val = val, s = s, e = e } end

                local function long_bracket_end_at(p)
                    if text:sub(p, p) ~= "[" then return nil end
                    local q = p + 1
                    while q <= len and text:sub(q, q) == "=" do q = q + 1 end
                    if text:sub(q, q) ~= "[" then return nil end
                    local eqs = text:sub(p + 1, q - 1)
                    local close = "]" .. eqs .. "]"
                    local found = text:find(close, q + 1, true)
                    return found and (found + #close - 1) or nil
                end

                while pos <= len do
                    local c = text:sub(pos, pos)
                    if is_space(c) then pos = pos + 1
                    elseif c == "-" and text:sub(pos + 1, pos + 1) == "-" then
                        pos = pos + 2
                        local lb_end = long_bracket_end_at(pos)
                        if lb_end then pos = lb_end + 1
                        else
                            local nl = text:find("\n", pos, true)
                            if nl then pos = nl + 1 else pos = len + 1 end
                        end
                    elseif c == "'" or c == '"' then
                        local quote = c; local s = pos; pos = pos + 1
                        while pos <= len do
                            local ch = text:sub(pos, pos)
                            if ch == "\\" then pos = pos + 2
                            elseif ch == quote then pos = pos + 1; break
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
                        while pos <= len do
                            local nc = text:sub(pos, pos)
                            if nc:match("^[A-Za-z0-9_%.]$") then
                                pos = pos + 1
                            elseif (nc == "+" or nc == "-") and text:sub(pos - 1, pos - 1):match("^[eEpP]$") then
                                pos = pos + 1
                            else
                                break
                            end
                        end
                        add("NUMBER", text:sub(s, pos - 1), s, pos - 1)
                    else
                        local map = { ["{"]="LBRACE", ["}"]="RBRACE", ["("]="LPAREN", [")"]="RPAREN", ["["]="LBRACK", ["]"]="RBRACK", ["="]="EQUALS", [","]="COMMA", [";"]="SEMI", ["."]="DOT", [":"]="COLON" }
                        add(map[c] or "OTHER", c, pos, pos); pos = pos + 1
                    end
                end
                return tokens
            end

            local function classify_raw(raw)
                local t = raw:gsub("^%s+", ""):gsub("%s+$", "")
                if t == "nil" then return "nil" end
                if t == "true" or t == "false" then return "bool" end
                if t:find("^%[=*%[") or t:find("^['\"]") then return "string" end
                if tonumber(t) ~= nil then return "number" end
                if t:match("^[A-Za-z_][A-Za-z0-9_]*$") then return "ident" end
                return "expr"
            end

            local function format_replacement(old_raw, new_value)
                if new_value == "__DELETE__" then return "nil" end
                local kind = classify_raw(old_raw)
                if kind == "nil" then
                    return new_value
                elseif kind == "ident" then
                    return new_value
                elseif kind == "bool" or kind == "number" or kind == "string" then
                    return new_value
                end
                error("Target value is a complex expression: [" .. tostring(old_raw) .. "]")
            end

            local function scope_string(parts) return table.concat(parts, "/") end

            local function find_rhs_end(tokens, i)
                local j = i; local depth = 0; local block_depth = 0; local rhs_end = i
                while j <= #tokens do
                    local tp = tokens[j].type; local val = tokens[j].val
                    local prev_tp = j > 1 and tokens[j-1].type or nil
                    
                    if tp == "IDENT" and prev_tp ~= "DOT" then
                        if (val == "function" or val == "if" or val == "do" or val == "repeat") then 
                            block_depth = block_depth + 1
                        elseif (val == "end" or val == "until") and block_depth > 0 then 
                            block_depth = block_depth - 1 
                        end
                    end
                    
                    if block_depth == 0 then
                        if tp == "LBRACE" or tp == "LPAREN" or tp == "LBRACK" then depth = depth + 1
                        elseif tp == "RBRACE" or tp == "RPAREN" or tp == "RBRACK" then if depth == 0 then break end; depth = depth - 1
                        elseif depth == 0 and (tp == "COMMA" or tp == "SEMI") then break end
                    end
                    rhs_end = j; j = j + 1
                end
                return rhs_end, j
            end

            local function key_at(tokens, i)
                local tok = tokens[i]
                if not tok then return nil, i end
                if tok.type == "IDENT" and tokens[i + 1] and tokens[i + 1].type == "EQUALS" then 
                    return tok.val, i + 2 
                end
                if tok.type == "LBRACK" and tokens[i + 1] and tokens[i + 1].type == "STRING" and tokens[i + 2] and tokens[i + 2].type == "RBRACK" and tokens[i + 3] and tokens[i + 3].type == "EQUALS" then
                    local str_val = tokens[i + 1].val
                    local decoder = assert(load("return " .. str_val, "key", "t", {}))
                    return decoder(), i + 4
                end
                return nil, i
            end

            -- Unified pass scans for ALL batch items simultaneously
            local peek_identifier
            local function parse_table(tokens, text, i, scope_parts, matches)
                if not tokens[i] or tokens[i].type ~= "LBRACE" then return i end
                i = i + 1
                local array_index = 1
                while i <= #tokens do
                    if tokens[i].type == "RBRACE" then return i + 1 end
                    if tokens[i].type == "COMMA" or tokens[i].type == "SEMI" then i = i + 1 goto continue end

                    local key, rhs = key_at(tokens, i)
                    if key then
                        local rhs_end, next_i = find_rhs_end(tokens, rhs)
                        if tokens[rhs] and tokens[rhs].type == "LBRACE" then
                            scope_parts[#scope_parts + 1] = key
                            parse_table(tokens, text, rhs, scope_parts, matches)
                            scope_parts[#scope_parts] = nil
                        else
                            local curr_scope = scope_string(scope_parts)
                            local lookup_key = curr_scope .. "\0" .. tostring(key)
                            local target_val = batch_lookup[lookup_key]
                            
                            if target_val then
                                local raw = text:sub(tokens[rhs].s, tokens[rhs_end].e)
                                matches[#matches + 1] = { 
                                    s = tokens[rhs].s, e = tokens[rhs_end].e, raw = raw, 
                                    new_val = target_val, lookup_key = lookup_key 
                                }
                            end
                        end
                        i = next_i
                    else
                        local key_str = tokens[i].type == "LBRACE" and peek_identifier(tokens, i) or tostring(array_index)
                        local rhs_end, next_i = find_rhs_end(tokens, i)
                        
                        if tokens[i] and tokens[i].type == "LBRACE" then
                            scope_parts[#scope_parts + 1] = key_str
                            parse_table(tokens, text, i, scope_parts, matches)
                            scope_parts[#scope_parts] = nil
                        else
                            local curr_scope = scope_string(scope_parts)
                            local lookup_key = curr_scope .. "\0" .. key_str
                            local target_val = batch_lookup[lookup_key]
                            
                            if target_val then
                                local raw = text:sub(tokens[i].s, tokens[rhs_end].e)
                                matches[#matches + 1] = { 
                                    s = tokens[i].s, e = tokens[rhs_end].e, raw = raw, 
                                    new_val = target_val, lookup_key = lookup_key 
                                }
                            end
                        end
                        
                        array_index = array_index + 1
                        i = next_i
                    end
                    ::continue::
                end
                return i
            end
            
            peek_identifier = function(tokens, start_idx)
                local k = start_idx + 1
                while k <= #tokens do
                    local token = tokens[k]
                    if token.type == "RBRACE" then break end
                    if token.type == "COMMA" or token.type == "SEMI" then
                        k = k + 1
                    else
                        local key, rhs = key_at(tokens, k)
                        if (key == "name" or key == "output" or key == "workspace") and tokens[rhs]
                           and (tokens[rhs].type == "STRING" or tokens[rhs].type == "NUMBER") then
                            return tostring(assert(load("return " .. tokens[rhs].val, "identifier", "t", {}))())
                        end
                        local _, next_k = find_rhs_end(tokens, key and rhs or k)
                        k = next_k
                    end
                end
                return nil
            end

            local function config_arg_index(tokens, i)
                if tokens[i] and tokens[i].type == "IDENT" and tokens[i].val == "hl" 
                   and tokens[i+1] and tokens[i+1].type == "DOT" 
                   and tokens[i+2] and tokens[i+2].type == "IDENT" then
                    local method = tokens[i+2].val
                    if tokens[i+3] and tokens[i+3].type == "LPAREN" then return i+4, method end
                    if tokens[i+3] and tokens[i+3].type == "LBRACE" then return i+3, method end
                end
                
                if tokens[i] and tokens[i].type == "IDENT" and tokens[i].val:match("^tui_.*_data$")
                   and tokens[i+1] and tokens[i+1].type == "EQUALS"
                   and tokens[i+2] and tokens[i+2].type == "LBRACE" then
                    local method = tokens[i].val:match("^tui_(.*)_data$")
                    if method == "workspace" or method == "window" or method == "layer" then
                        method = method .. "_rule"
                    end
                    return i+2, method
                end
                
                return nil, nil
            end

            local target_text = read_file(src_path)
            if not target_text then os.exit(4) end
            local target_tokens = tokenize(target_text)
            local sites = {}
            for _, call in ipairs(batch.calls or {}) do
                if call.file == src_path then
                    local key = call.line .. "\0" .. call.method
                    sites[key] = sites[key] or {}
                    table.insert(sites[key], call.id)
                end
            end
            local line, cursor = 1, 1
            local source_counts = {}
            for index, token in ipairs(target_tokens) do
                while cursor < token.s do
                    local next_line = target_text:find("\n", cursor, true)
                    if not next_line or next_line >= token.s then cursor = token.s; break end
                    line, cursor = line + 1, next_line + 1
                end
                token.line = line
                local argument, method = config_arg_index(target_tokens, index)
                if argument and token.val == "hl" then
                    local site = line .. "\0" .. method
                    source_counts[site] = (source_counts[site] or 0) + 1
                end
            end

            local matches = {}
            local idx = 1
            while idx <= #target_tokens do
                local arg_idx, method = config_arg_index(target_tokens, idx)
                local line = target_tokens[idx].line
                local site = line .. "\0" .. (method or "")
                local ids = sites[site]
                if arg_idx and target_tokens[idx].val ~= "hl" then
                    -- Legacy data tables have their definition on another line.
                    local name = peek_identifier(target_tokens, arg_idx)
                    ids = {}
                    for _, call in ipairs(batch.calls or {}) do
                        if call.method == method and (not name or call.id == name) then
                            ids[#ids + 1] = call.id
                        end
                    end
                end
                -- A source line can execute repeatedly or contain several calls.
                -- Without a unique runtime identity, refuse a requested edit.
                if ids and (#ids > 1 or (source_counts[site] or 0) > 1) then
                    local ambiguous = false
                    if method == "config" then
                        local candidates = {}
                        parse_table(target_tokens, target_text, arg_idx, {}, candidates)
                        ambiguous = #candidates > 0
                    else
                        for _, id in ipairs(ids) do
                            local scope = method .. "/" .. id
                            for _, item in ipairs(batch) do
                                if item.scope == scope or item.scope:sub(1, #scope + 1) == scope .. "/" then
                                    ambiguous = true
                                end
                            end
                        end
                    end
                    if ambiguous then
                        io.stderr:write("Ambiguous repeated Lua call on line ", line, ".\n")
                        os.exit(3)
                    end
                    ids = nil
                end
                if arg_idx and ids and #ids == 1 then
                    if method == "config" then
                        parse_table(target_tokens, target_text, arg_idx, {}, matches)
                    elseif method == "env" then
                        local scope = { method, ids[1] }
                        local key_end, next_arg = find_rhs_end(target_tokens, arg_idx)
                        local value_arg = next_arg + 1
                        local value_end = find_rhs_end(target_tokens, value_arg)
                        for field, range in pairs({key = {arg_idx, key_end}, value = {value_arg, value_end}}) do
                            local lookup_key = scope_string(scope) .. "\0" .. field
                            local target_val = batch_lookup[lookup_key]
                            if target_val and target_tokens[range[1]] and target_tokens[range[2]] then
                                matches[#matches + 1] = {
                                    s = target_tokens[range[1]].s, e = target_tokens[range[2]].e,
                                    raw = target_text:sub(target_tokens[range[1]].s, target_tokens[range[2]].e),
                                    new_val = target_val, lookup_key = lookup_key,
                                }
                            end
                        end
                    elseif method == "bind" or method == "unbind" then
                        local comma_count, k, depth, block_depth = 0, arg_idx, 0, 0
                        while k <= #target_tokens do
                            local t = target_tokens[k].type
                            local val = target_tokens[k].val
                            local prev_tp = k > 1 and target_tokens[k-1].type or nil
                            
                            if t == "IDENT" and prev_tp ~= "DOT" then
                                if (val == "function" or val == "if" or val == "do" or val == "repeat") then
                                    block_depth = block_depth + 1
                                elseif (val == "end" or val == "until") and block_depth > 0 then
                                    block_depth = block_depth - 1
                                end
                            end

                            if t == "LPAREN" or t == "LBRACE" or t == "LBRACK" then depth = depth + 1
                            elseif t == "RPAREN" or t == "RBRACE" or t == "RBRACK" then depth = depth - 1
                            elseif depth == 0 and block_depth == 0 and t == "COMMA" then
                                comma_count = comma_count + 1
                                if comma_count == 2 and target_tokens[k+1] and target_tokens[k+1].type == "LBRACE" then
                                    local bind_key = ids[1]
                                    parse_table(target_tokens, target_text, k+1, { method, bind_key }, matches)
                                    break
                                end
                            elseif depth < 0 then break end
                            k = k + 1
                        end
                    else
                        local id = ids[1]
                        parse_table(target_tokens, target_text, arg_idx, { method, id }, matches)
                    end
                end
                idx = idx + 1
            end

            io.stderr:write("[Telemetry] Found " .. #matches .. " AST match(es) for batch.\n")

            if #matches == 0 then os.exit(1) end
            
            local matched_tracker = {}
            
            table.sort(matches, function(a, b) return a.s < b.s end)
            -- Apply replacements in reverse order to preserve string indexing
            for j = #matches, 1, -1 do
                local m = matches[j]
                matched_tracker[m.lookup_key] = true
                
                io.stderr:write("[Telemetry] Match " .. j .. ": " .. m.raw .. " -> " .. tostring(m.new_val) .. "\n")
                
                local ok, repl_or_err = pcall(format_replacement, m.raw, m.new_val)
                if not ok then
                    io.stderr:write(tostring(repl_or_err), "\n")
                    os.exit(3)
                end
                target_text = target_text:sub(1, m.s - 1) .. repl_or_err .. target_text:sub(m.e + 1)
            end
            
            -- Refuse malformed output before staging a replacement.
            local valid, syntax_error = load(target_text, "@" .. src_path, "t", {})
            if not valid then io.stderr:write(syntax_error, "\n"); os.exit(3) end

            -- Inform Python specifically which keys were successfully patched
            local function hex(value)
                return (value:gsub(".", function(char) return string.format("%02x", char:byte()) end))
            end
            for k, _ in pairs(matched_tracker) do
                local scope, key = k:match("^(.-)%z(.*)$")
                io.stderr:write("[MATCHED] " .. hex(scope) .. " " .. hex(key) .. "\n")
            end
            
            local out_f = io.open(out_path, "wb")
            if not out_f then os.exit(5) end
            out_f:write(target_text)
            out_f:close()
            os.exit(0)
            """
            
            for src_file in self.loaded_files:
                target_path = Path(src_file)
                if not target_path.exists() or not target_path.is_file(): 
                    continue
                
                out_fd, raw_out_path = tempfile.mkstemp(dir=target_path.parent, text=True)
                os.close(out_fd)
                out_path = Path(raw_out_path)
                temp_files_created.append(out_path)

                args = [self.lua_bin, "-E", "-", str(target_path), str(batch_path), str(out_path)]
                
                res = subprocess.run(
                    args, 
                    input=lua_mutator, 
                    text=True, 
                    encoding='utf-8', 
                    capture_output=True, 
                    timeout=5.0
                )
                
                debug_output += res.stderr
                
               # Parse Lua Telemetry to verify exactly which items succeeded
                for line in res.stderr.splitlines():
                    if line.startswith("[MATCHED] "):
                        try:
                            scope_hex, key_hex = line.removeprefix('[MATCHED] ').split(' ', 1)
                            successful_commits.add((bytes.fromhex(key_hex).decode('utf-8'), bytes.fromhex(scope_hex).decode('utf-8')))
                        except ValueError:
                            pass

                if res.returncode == 0:
                    pending_replacements.append((out_path, target_path, src_file))
                elif res.returncode == 1:
                    continue # Valid constraint: the batch didn't hit any keys inside this specific file
                else:
                    status_msg = f"Lua Mutator Error {res.returncode} in {src_file}"
                    break # Abort entire transaction immediately to prevent tearing between files
            else:
                if pending_replacements and len(successful_commits) == len(changes):
                    success = True
                elif pending_replacements:
                    status_msg = f'Batch aborted: found {len(successful_commits)}/{len(changes)} items.'

        except subprocess.TimeoutExpired:
            success = False
            status_msg = "Execution Error: Lua mutator timed out."
        except (OSError, subprocess.SubprocessError) as e:
            success = False
            status_msg = f"Execution Error: {e}"
            
        finally:
            if success:
                try:
                    # Complete metadata and file sync for every staged output first.
                    for tmp_out, trg_path, _ in pending_replacements:
                        info = trg_path.stat()
                        temp_info = tmp_out.stat()
                        if (temp_info.st_uid, temp_info.st_gid) != (info.st_uid, info.st_gid):
                            os.chown(tmp_out, info.st_uid, info.st_gid)
                        tmp_out.chmod(stat.S_IMODE(info.st_mode))
                        with tmp_out.open('rb') as stream:
                            os.fsync(stream.fileno())
                    for src_f, before in snapshots.items():
                        if current_stamp(Path(src_f)) != stamp(before):
                            raise OSError(f'File {src_f} changed while preparing edits. Reload required.')
                    for tmp_out, trg_path, src_f in pending_replacements:
                        tmp_out.replace(trg_path)
                        committed_count += 1
                        info = trg_path.stat()
                        self.file_mtimes[src_f] = info.st_mtime
                        self._file_stamps[src_f] = (info.st_mtime, stamp(info))
                        directory_fd = os.open(trg_path.parent, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                    
                    if len(successful_commits) == len(changes):
                        status_msg = f"Successfully batched {len(changes)} commits."
                    else:
                        status_msg = f"Partial success: saved {len(successful_commits)}/{len(changes)} items."
                        
                except OSError as e:
                    success = False
                    status_msg = f"Commit failed after {committed_count}/{len(pending_replacements)} file replacements; reload required: {e}"

            # Clean up uncommitted outputs without hiding the original error.
            for tmp_file in temp_files_created:
                try:
                    tmp_file.unlink(missing_ok=True)
                except OSError:
                    pass
                    
            if batch_path:
                try:
                    batch_path.unlink(missing_ok=True)
                except OSError:
                    pass

        if success:
            # Re-evaluate using Lua's numeric semantics and refresh runtime identities.
            self.load_state()
            if not self.loaded_files:
                return False, "Files saved, but Lua state refresh failed. Reload required.", debug_output
            return True, status_msg, debug_output
            
        if not pending_replacements and status_msg == "Failed":
            return False, "No items in the batch were found in the configuration tree.", debug_output
            
        return False, status_msg, debug_output
