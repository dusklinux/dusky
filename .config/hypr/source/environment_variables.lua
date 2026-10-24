-- ~/.config/hypr/source/environment_variables.lua
-- Native Hyprland / systemd 262+ / Arch rolling
-- No UWSM; Wayland-only backends; application-specific rendering defaults.
-- @diagnostic disable: undefined-global

local getenv = os.getenv
local home = getenv("HOME") or ""
local before, managed = {}, {}
local function hl_env(key, value)
  if not managed[key] then
    before[key] = getenv(key)
    managed[key] = true
  end
  hl.env(key, value)
end

local function has_utf8(s)
  return s and s:lower():find("utf%-?8") ~= nil
end

-- 1. Locale: supply a UTF-8 character locale without overriding explicit choices.
do
  local lang = getenv("LANG") or ""
  local all = getenv("LC_ALL") or ""
  local ctype = getenv("LC_CTYPE") or ""
  if all == "" and ctype == "" and not has_utf8(lang) then
    hl_env("LC_CTYPE", "C.UTF-8")
  end
end

-- 2. Default thread count; preserve explicit OpenMP settings, including nested lists.
do
  local threads = getenv("OMP_NUM_THREADS")
  if not threads or threads == "" then
    local h = io.popen("nproc 2>/dev/null")
    if h then
      local n = h:read("*n")
      h:close()
      if n and n >= 1 and n % 1 == 0 then hl_env("OMP_NUM_THREADS", tostring(n)) end
    end
  end
end

-- 3. Dynamic PATH - prepend ~/.local/bin and ~/.cargo/bin safely, deduped
do
  local existing = getenv("PATH") or "/usr/local/sbin:/usr/local/bin:/usr/bin"
  local seen, paths = {}, {}
  local function add(p)
    if p ~= "" and not seen[p] then
      table.insert(paths, p)
      seen[p] = true
    end
  end
  if home ~= "" then
    add(home .. "/.local/bin")
    add(home .. "/.cargo/bin")
  end
  for p in existing:gmatch("[^:]+") do add(p) end
  hl_env("PATH", table.concat(paths, ":"))
end

-- 4. Session identification - native, not uwsm
hl_env("DESKTOP_SESSION", "hyprland")
hl_env("XDG_CURRENT_DESKTOP", "Hyprland")
hl_env("XDG_SESSION_TYPE", "wayland")
hl_env("XDG_SESSION_DESKTOP", "Hyprland")
hl_env("XDG_SESSION_CLASS", "user") -- needed for localsearch ConditionEnvironment
hl_env("XDG_MENU_PREFIX", "arch-") -- fixes empty Open With in Dolphin/digiKam on Arch

-- 5. Native Wayland backends and toolkit appearance.
hl_env("GDK_BACKEND", "wayland")
hl_env("QT_QPA_PLATFORM", "wayland")
hl_env("QT_QPA_PLATFORMTHEME", "qt6ct")
hl_env("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")
hl_env("QT_QUICK_CONTROLS_STYLE", "Fusion")
-- Let Qt handle native scaling and GTK select a renderer for the available hardware.
hl_env("SDL_VIDEODRIVER", "wayland") -- SDL2 API, including sdl2-compat
hl_env("SDL_VIDEO_DRIVER", "wayland") -- SDL3 API
hl_env("CLUTTER_BACKEND", "wayland")

-- 6. Application integration. Modern Electron uses XDG_SESSION_TYPE directly.
hl_env("MOZ_ENABLE_WAYLAND", "1")
hl_env("GTK_USE_PORTAL", "1") -- request portal file pickers in GTK3 applications

-- 7. Appearance / cursor
hl_env("XCURSOR_THEME", "Bibata-Modern-Classic")
hl_env("XCURSOR_SIZE", "18")
hl_env("HYPRCURSOR_SIZE", "18")

-- 8. Apps / defaults
do
  -- default_apps.lua is loaded first; follow the switcher's current selection.
  -- GUI editors must keep CLI callers (git, crontab, etc.) waiting for edits.
  local editor = textEditor or "mousepad"
  local wait_flags = {
    ["mousepad"] = " --disable-server",
    ["gnome-text-editor"] = " --standalone",
    ["code"] = " --wait",
    ["codium"] = " --wait",
    ["zeditor"] = " --wait",
    ["kate"] = " --block",
  }
  local command = editor .. (wait_flags[editor] or "")
  local browser_command = browser or "firefox"
  local terminal_command = "xdg-terminal-exec"
  hl_env("TERMINAL", terminal_command)
  hl_env("EDITOR", command)
  hl_env("VISUAL", command)
  hl_env("BROWSER", browser_command)
end
hl_env("LIBVIRT_DEFAULT_URI", "qemu:///system")

-- 9. Clipboard persistence - dynamic path from toggler
-- Reads $XDG_CONFIG_HOME/dusky/settings/cliphist_db_env (default: ~/.config).
-- Populates Hyprland's session environment so apps launched via keybinds inherit it.
-- The daemon and new interactive shells independently refresh from the same file.
do
  local config_home = getenv("XDG_CONFIG_HOME")
  if not config_home or config_home == "" then config_home = home .. "/.config" end
  local cache_home = getenv("XDG_CACHE_HOME")
  if not cache_home or cache_home == "" then cache_home = home .. "/.cache" end
  local data_home = getenv("XDG_DATA_HOME")
  if not data_home or data_home == "" then data_home = home .. "/.local/share" end
  -- Export the same directories to apps and the user service manager.
  hl_env("XDG_CONFIG_HOME", config_home)
  hl_env("XDG_CACHE_HOME", cache_home)
  hl_env("XDG_DATA_HOME", data_home)
  local path = cache_home .. "/cliphist/db"
  local f = io.open(config_home .. "/dusky/settings/cliphist_db_env", "r")
  if f then
    for line in f:lines() do
      local assignment = line:gsub("^%s*", ""):gsub("^export%s+", "")
      local value = assignment:match("^CLIPHIST_DB_PATH%s*=%s*(.*)$")
      if value then
        value = value:match('^"([^"]*)"')
             or value:match("^'([^']*)'")
             or value:match("^[^%s]*")
        if value:match("^/.") then path = value end
      end
    end
    f:close()
  end
  hl_env("CLIPHIST_DB_PATH", path)
end

-- Sync final values after the user overlay and hl.env changes have been applied.
-- Only changed variables are sent; existing applications keep their environment.
local function sync_environment()
  local keys = {}
  for key in pairs(managed) do
    local value = getenv(key)
    if value ~= nil and value ~= before[key] then keys[#keys + 1] = key end
  end
  if #keys == 0 then return end
  table.sort(keys)
  local args = {"dbus-update-activation-environment --systemd"}
  for _, key in ipairs(keys) do
    local value = getenv(key)
    args[#args + 1] = key .. "='" .. value:gsub("'", "'\\''") .. "'"
    before[key] = value
  end
  hl.exec_cmd(table.concat(args, " "))
end
hl.on("config.reloaded", sync_environment)
hl.on("hyprland.start", sync_environment)
