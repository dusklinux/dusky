#!/usr/bin/env python3
import os
import stat
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine

# =============================================================================
# [ BLOCK 1: THE ENGINE ]
# Optimized for Modern Python 3.14 / Arch Linux.
# Unified Pathlib usage, refined subprocess handling, and modernized typing.
# =============================================================================

class HyprlandLuaEngine(BaseEngine):
    # Pre-compiled frozenset for C-speed hex validation evaluation
    _HEX_CHARS = frozenset("0123456789abcdefABCDEF")
    
    # Pre-compiled set of special floats that Python parses but Lua does not naturally handle
    _SPECIAL_FLOATS = {"inf", "-inf", "infinity", "-infinity", "nan"}

    def __init__(self, config_path: str = "~/Documents/hyprland.lua"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.config_dir = self.config_path.parent
        self.lua_bin = self._find_lua()
        self.cache: dict[str, Any] = {}
        self.loaded_files: list[str] = []
        self.file_mtimes: dict[str, float] = {}

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
                        [cmd_path, "-e", "assert(_VERSION:match('5%.[4-9]'))"],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    return cmd_path
                except (subprocess.CalledProcessError, FileNotFoundError, OSError):
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
        if not self.config_path.exists(): 
            return {}

        self.file_mtimes[str(self.config_path)] = self.config_path.stat().st_mtime

        # THE HYPRLAND v0.55.0+ DYNAMIC SANDBOX
        lua_evaluator = r"""
        local main_path = arg[1]
        local config_dir = arg[2]
        local config_root = {}
        local loaded_files = {main_path}
        
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
                    return function(tbl) if type(tbl) == "table" then deep_merge(config_root, tbl) end end
                elseif key == "bind" or key == "unbind" then
                    return function(bind_key, dispatcher, flags)
                        if type(flags) == "table" then
                            flags._bind_key = bind_key
                            if not config_root[key] then config_root[key] = {} end
                            table.insert(config_root[key], flags)
                        end
                    end
                else
                    return function(tbl) append_list(key, tbl) end
                end
            end
        })

        local safe_env = { 
            hl = hl, math = math, string = string, table = table, type = type, 
            pairs = pairs, ipairs = ipairs, tostring = tostring, tonumber = tonumber, 
            HOME = os.getenv("HOME") or "",
            os = {getenv = function() return nil end},
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
            table.insert(loaded_files, path)
            local chunk = loadfile(path, "t", safe_env)
            if chunk then
                local ok, err = pcall(chunk)
                if not ok then
                    io.stderr:write("[sandbox] dofile skipped: " .. tostring(err) .. "\n")
                end
            end
        end
        
        safe_env.require = function(path) return safe_env.dofile(path .. ".lua") end
        
        local chunk = loadfile(main_path, "t", safe_env)
        if chunk then pcall(chunk) end
        
        local out_state = {}
        local function escape_str(s) 
            s = s:gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n'):gsub('\r', '\\r'):gsub('\t', '\\t')
            s = s:gsub('[%c]', function(c) return string.format('\\u%04x', string.byte(c)) end)
            return '"' .. s .. '"' 
        end

        -- SECURE WALK FUNCTION: Preserves data types for Python JSON loader
        local function walk(t, scope, seen) 
            seen = seen or {}
            if seen[t] then return end
            seen[t] = true
            
            for k, v in pairs(t) do 
                if type(k) == "string" or type(k) == "number" then 
                    local str_k = tostring(k)
                    local is_ident_key = false
                    
                    if type(v) == "table" and type(k) == "number" then
                        local id = v.name or v.output or v._bind_key
                        if id then str_k = tostring(id) end
                    end

                    if type(k) == "string" and (k == "name" or k == "output" or k == "_bind_key") then
                        is_ident_key = true
                    end

                    if not is_ident_key then
                        local new_scope = scope == "" and str_k or (scope .. "/" .. str_k)
                        if type(v) == "table" then 
                            walk(v, new_scope, seen) 
                        else 
                            local val_str
                            if type(v) == "string" then val_str = escape_str(v)
                            elseif type(v) == "boolean" then val_str = tostring(v)
                            elseif type(v) == "number" then val_str = tostring(v)
                            else val_str = escape_str(tostring(v)) end
                            table.insert(out_state, escape_str(new_scope)..":"..val_str) 
                        end 
                    end
                end 
            end 
        end
        walk(config_root, "")
        
        local out_files = {}
        for _, f in ipairs(loaded_files) do table.insert(out_files, escape_str(f)) end
        
        io.stdout:write('{"state": {' .. table.concat(out_state, ",") .. '}, "files": [' .. table.concat(out_files, ",") .. ']}')
        """
        
        try:
            res = subprocess.run(
                [self.lua_bin, "-", str(self.config_path), str(self.config_dir)], 
                input=lua_evaluator, 
                text=True, 
                encoding='utf-8', 
                capture_output=True, 
                timeout=5.0
            )
            
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                self.cache = data.get("state", {})
                
                raw_files = data.get("files", [str(self.config_path)])
                self.loaded_files = [f for f in raw_files if self._is_safe_path(f)]
                
                for f in self.loaded_files:
                    path_obj = Path(f)
                    if path_obj.exists():
                        self.file_mtimes[f] = path_obj.stat().st_mtime
                        
                return self.cache
            else:
                print(f"Load Error (Return Code {res.returncode}): {res.stderr}")
        except json.JSONDecodeError as e:
            print(f"Failed to parse Lua state JSON: {e}")
        except subprocess.TimeoutExpired:
            print("Load Exception: Lua evaluation timed out.")
        except (OSError, subprocess.SubprocessError) as e: 
            print(f"Load Exception: {e}")
            
        return {}
    
    def _is_raw_lua_val(self, val: str) -> bool:
        if val in {"true", "false", "nil", "__DELETE__"}: 
            return True
        
        # Hex validation using native set subset execution (fast C-level)
        if val.startswith("0x") and len(val) > 2 and set(val[2:]).issubset(self._HEX_CHARS):
            return True

        # Ensure no accidental whitespace stripping or special IEEE 754 constants corrupt Lua types
        if val == val.strip() and val.lower() not in self._SPECIAL_FLOATS:
            try: 
                float(val)
                return True
            except ValueError: 
                pass
            
        return False

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        """
        Proxy method. Routes single mutations through the unified high-speed batch architecture.
        Guarantees zero behavioral drift between single UI edits and bulk UI presets.
        """
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        """
        TRUE ATOMIC AST BATCHING.
        Compiles the entire batch payload into a single Lua table and executes a SINGLE 
        O(1) pass per configuration file. Eliminates previous Subprocess/IO bottlenecks entirely.
        """
        if not changes:
            return True, "No pending changes.", ""
            
        if not self.loaded_files: 
            self.loaded_files = [str(self.config_path)]

        # Concurrency safety check
        for src_file in self.loaded_files:
            target_path = Path(src_file)
            if target_path.exists():
                cached_mtime = self.file_mtimes.get(src_file)
                if cached_mtime and target_path.stat().st_mtime > cached_mtime:
                    return False, f"File {src_file} modified externally. Reload required.", ""

        # Build secure, serialized Lua batch table
        lua_table = "return {\n"
        for key, scope, new_value, item_type in changes:
            if isinstance(new_value, str) and new_value.startswith("__VAR__"):
                val_str = new_value[7:]
            elif item_type == "bool":
                val_str = "true" if str(new_value).lower() in ("true", "1", "yes", "on", "t", "y") else "false"
            elif item_type in ("int", "float") or self._is_raw_lua_val(str(new_value)):
                val_str = new_value
            else:
                val_str = json.dumps(new_value, ensure_ascii=False)
            
            lua_table += f"  {{ key = {json.dumps(key, ensure_ascii=False)}, scope = {json.dumps(scope, ensure_ascii=False)}, val = {json.dumps(val_str, ensure_ascii=False)} }},\n"
        lua_table += "}\n"

        status_msg = "Failed"
        debug_output = ""
        success = False
        
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

            local files = {}
            for k = 4, #arg do table.insert(files, arg[k]) end

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
                if t == "true" or t == "false" or t == "nil" then return "bool" end
                if t:find("^%[=*%[") or t:find("^['\"]") then return "string" end
                if tonumber(t) ~= nil then return "number" end
                if t:match("^[A-Za-z_][A-Za-z0-9_]*$") then return "ident" end
                return "expr"
            end

            local function format_replacement(old_raw, new_value)
                if new_value == "__DELETE__" then return "nil" end
                local kind = classify_raw(old_raw)
                if kind == "ident" then
                    return new_value
                elseif kind == "bool" then
                    if new_value == "true" or new_value == "false" or new_value == "nil" then return new_value end
                    return new_value == "0" and "false" or "true"
                elseif kind == "number" then
                    return new_value
                elseif kind == "string" then
                    local t = old_raw:gsub("^%s+", ""):gsub("%s+$", "")
                    if t:sub(1,1) == "[" then
                        local stripped_val = new_value:gsub('^"', ''):gsub('"$', '')
                        local open_bracket = t:match("^(%[=*%[)")
                        if open_bracket then
                            local close_bracket = open_bracket:gsub("%[", "%]")
                            if stripped_val:find(close_bracket, 1, true) then
                                return new_value
                            end
                            return open_bracket .. stripped_val .. close_bracket
                        end
                    end
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
                    local clean_key = str_val:match("^['\"](.-)['\"]$")
                    if not clean_key then clean_key = str_val:match("^%[=*%[(.-)%]=*%]$") end
                    return clean_key or str_val, i + 4
                end
                return nil, i
            end

            -- Unified pass scans for ALL batch items simultaneously
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
                        local key_str = tostring(array_index)
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
            
            local function peek_identifier(tokens, start_idx)
                local k = start_idx + 1
                local depth = 0
                while k <= #tokens do
                    local t = tokens[k].type
                    if t == "LBRACE" then depth = depth + 1
                    elseif t == "RBRACE" then
                        if depth == 0 then break end
                        depth = depth - 1
                    elseif depth == 0 and t == "IDENT" then
                        if (tokens[k].val == "name" or tokens[k].val == "output") 
                           and tokens[k+1] and tokens[k+1].type == "EQUALS" 
                           and tokens[k+2] and tokens[k+2].type == "STRING" then
                            return tokens[k+2].val:match("^['\"](.-)['\"]$")
                        end
                    end
                    k = k + 1
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

            local method_counters = {}
            local target_tokens = nil
            local target_text = nil
            
            for _, filepath in ipairs(files) do
                local text = read_file(filepath)
                local toks = tokenize(text)
                
                if filepath == src_path then
                    target_text = text
                    target_tokens = toks
                    break
                end
                
                local idx = 1
                while idx <= #toks do
                    local arg_idx, method = config_arg_index(toks, idx)
                    if arg_idx and method ~= "config" then
                        method_counters[method] = (method_counters[method] or 0) + 1
                    end
                    idx = idx + 1
                end
            end
            
            if not target_text then os.exit(4) end
            
            local matches = {}
            local idx = 1
            while idx <= #target_tokens do
                local arg_idx, method = config_arg_index(target_tokens, idx)
                if arg_idx then
                    if method == "config" then
                        parse_table(target_tokens, target_text, arg_idx, {}, matches)
                    elseif method == "bind" or method == "unbind" then
                        local comma_count, k, arg_idx_runner, depth = 0, arg_idx, 0, 0
                        while k <= #target_tokens do
                            local t = target_tokens[k].type
                            if t == "LPAREN" or t == "LBRACE" or t == "LBRACK" then depth = depth + 1
                            elseif t == "RPAREN" or t == "RBRACE" or t == "RBRACK" then depth = depth - 1
                            elseif depth == 0 and t == "COMMA" then
                                comma_count = comma_count + 1
                                if comma_count == 2 and target_tokens[k+1] and target_tokens[k+1].type == "LBRACE" then
                                    local bind_key = target_tokens[arg_idx].val:match("^['\"](.-)['\"]$") or "unknown"
                                    parse_table(target_tokens, target_text, k+1, { method, bind_key }, matches)
                                    break
                                end
                            elseif depth < 0 then break end
                            k = k + 1
                        end
                    else
                        local id = peek_identifier(target_tokens, arg_idx)
                        if not id then 
                            method_counters[method] = (method_counters[method] or 0) + 1
                            id = tostring(method_counters[method])
                        end
                        parse_table(target_tokens, target_text, arg_idx, { method, id }, matches)
                    end
                end
                idx = idx + 1
            end

            io.stderr:write("[Telemetry] Found " .. #matches .. " AST match(es) for batch.\n")

            if #matches == 0 then os.exit(1) end
            
            local matched_tracker = {}
            
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
            
            -- Inform Python specifically which keys were successfully patched
            for k, _ in pairs(matched_tracker) do
                io.stderr:write("[MATCHED] " .. k .. "\n")
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

                try:
                    out_path.chmod(stat.S_IMODE(target_path.stat().st_mode))
                except OSError:
                    pass

                args = [self.lua_bin, "-", str(target_path), str(batch_path), str(out_path)] + self.loaded_files
                
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
                            scope_str, key_str = line.split("[MATCHED] ")[1].strip().split("\0", 1)
                            successful_commits.add((key_str, scope_str))
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
                if pending_replacements:
                    success = True

        except subprocess.TimeoutExpired:
            success = False
            status_msg = "Execution Error: Lua mutator timed out."
        except (OSError, subprocess.SubprocessError) as e:
            success = False
            status_msg = f"Execution Error: {e}"
            
        finally:
            if success:
                try:
                    for tmp_out, trg_path, src_f in pending_replacements:
                        tmp_out.replace(trg_path)
                        self.file_mtimes[src_f] = trg_path.stat().st_mtime
                    
                    if len(successful_commits) == len(changes):
                        status_msg = f"Successfully batched {len(changes)} commits."
                    else:
                        status_msg = f"Partial success: saved {len(successful_commits)}/{len(changes)} items."
                        
                except OSError as e:
                    success = False
                    status_msg = f"Transaction Commit Error: {e}"

            # Modern Python 3.14+ file cleanup logic
            for tmp_file in temp_files_created:
                tmp_file.unlink(missing_ok=True)
                    
            if batch_path:
                batch_path.unlink(missing_ok=True)

        if success:
            return True, status_msg, debug_output
            
        if not pending_replacements and status_msg == "Failed":
            return False, "No items in the batch were found in the configuration tree.", debug_output
            
        return False, status_msg, debug_output
