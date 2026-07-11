-- ~/.config/hypr/source/environment_variables.lua
-- Native Hyprland 0.55.4+ / systemd 261+ / Arch bleeding-edge
-- No UWSM, no GPU vars. Performance: locals cached, single io.popen, deduped PATH
-- @diagnostic disable: undefined-global

local hl_env = hl.env
local getenv = os.getenv
local home = getenv("HOME") or ""

local function has_utf8(s)
  return s and s:lower():find("utf%-?8") ~= nil
end

-- 1. Locale: keep user's LANG, ensure CTF rendering for btop etc.
-- Original UWSM case $LANG in *utf8* else LC_CTYPE=C.UTF-8
do
  local lang = getenv("LANG") or ""
  if not has_utf8(lang) then
    hl_env("LC_CTYPE", "C.UTF-8")
  end
end

-- 2. Dynamic thread count - requirement: io.popen("nproc")
do
  local count = "4"
  local h = io.popen("nproc 2>/dev/null")
  if h then
    local n = h:read("*n")
    h:close()
    if n and n > 0 then count = tostring(n) end
  end
  hl_env("OMP_NUM_THREADS", count)
end

-- 3. Dynamic PATH - prepend ~/.local/bin and ~/.cargo/bin safely, deduped
do
  local existing = getenv("PATH") or "/usr/local/sbin:/usr/local/bin:/usr/bin"
  local seen = {}
  for p in existing:gmatch("[^:]+") do seen[p] = true end
  local prepend = {}
  local function add(p)
    if p ~= "" and not seen[p] then
      table.insert(prepend, p)
      seen[p] = true
    end
  end
  add(home.. "/.local/bin")
  add(home.. "/.cargo/bin")
  hl_env("PATH", table.concat(prepend, ":").. ":".. existing)
end

-- 4. Session identification - native, not uwsm
hl_env("DESKTOP_SESSION", "hyprland")
hl_env("XDG_CURRENT_DESKTOP", "Hyprland")
hl_env("XDG_SESSION_TYPE", "wayland")
hl_env("XDG_SESSION_DESKTOP", "Hyprland")
hl_env("XDG_SESSION_CLASS", "user") -- needed for localsearch ConditionEnvironment
hl_env("XDG_MENU_PREFIX", "arch-") -- fixes empty Open With in Dolphin/digiKam on Arch

-- 5. Toolkit / Wayland native backends - merged from your UWSM env
hl_env("GDK_BACKEND", "wayland,x11,*")
hl_env("QT_QPA_PLATFORM", "wayland;xcb")
hl_env("QT_QPA_PLATFORMTHEME", "qt6ct")
hl_env("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")
hl_env("QT_QUICK_CONTROLS_STYLE", "Fusion")
hl_env("QT_WAYLAND_RECONNECT_AFTER_VT_SWITCH", "1")
hl_env("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
hl_env("GSK_RENDERER", "ngl")
hl_env("SDL_VIDEODRIVER", "wayland,x11")
hl_env("SDL_VIDEO_DRIVER", "wayland,x11") -- compat for old SDL2 builds
hl_env("CLUTTER_BACKEND", "wayland")
hl_env("_JAVA_AWT_WM_NONREPARENTING", "1")

-- 6. Modern Wayland hints - bleeding-edge Arch defaults
-- Electron: --ozone-platform-hint=auto can be added on Electron 20+
hl_env("ELECTRON_OZONE_PLATFORM_HINT", "auto")
-- Firefox 121+ defaults to Wayland already, explicit var keeps ESR and forks safe
hl_env("MOZ_ENABLE_WAYLAND", "1")
hl_env("GTK_USE_PORTAL", "1") -- use xdg-desktop-portal file picker on Hyprland

-- 7. Appearance / cursor
hl_env("XCURSOR_THEME", "Bibata-Modern-Classic")
hl_env("XCURSOR_SIZE", "18")
hl_env("HYPRCURSOR_SIZE", "18")

-- 8. Apps / defaults
hl_env("TERMINAL", "xdg-terminal-exec")
hl_env("EDITOR", "nvim")
hl_env("VISUAL", "nvim")
hl_env("LIBVIRT_DEFAULT_URI", "qemu:///system")

-- 9. Clipboard persistence - dynamic path from toggler
do
  local f = io.open(home.. "/.config/dusky/settings/cliphist_db_env", "r")
  if f then
    local content = f:read("*a")
    f:close()
    local path = content:match('CLIPHIST_DB_PATH%s*=%s*"([^"]+)"')
    if path and path ~= "" then
      hl_env("CLIPHIST_DB_PATH", path)
    end
  end
end
