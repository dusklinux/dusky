#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: MAKO MATUGEN TEMPLATE SCHEMA (INI PARADIGM)
===============================================================================
Targeting the Matugen pre-processor template. Allows granular control over 
base geometry, urgency states, and all Dusky custom applet modules.
===============================================================================
"""

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING (REQUIRED)
# =============================================================================
ENGINE_TYPE = "ini"                        
TARGET_FILE = "~/.config/matugen/templates/mako.ini"      
APP_TITLE = "Mako Template Config"                 

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"                      
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json" 

ENABLE_USER_PRESETS = True                 
USER_PRESETS_TAB = "Profiles"              


# Displays a popup when the TUI is first launched
GLOBAL_POPUP = {
    "title": "Color Application Notice",
    "message": "To apply color changes, you must regenerate them by changing your wallpaper or using 'Regenerate' in the Profiles tab.",
    "level": "info",           
    "require_confirm": False,  
    "cancel_quits": False      
}

# =============================================================================
# 3. GLOBAL COLOR PALETTES (MATUGEN + HARDCODED)
# =============================================================================
COLOR_OPTIONS = [
    # --- Matugen Material Design Variables ---
    "{{colors.primary.default.hex}}", "{{colors.on_primary.default.hex}}",
    "{{colors.primary_container.default.hex}}", "{{colors.on_primary_container.default.hex}}",
    "{{colors.secondary.default.hex}}", "{{colors.on_secondary.default.hex}}",
    "{{colors.secondary_container.default.hex}}", "{{colors.on_secondary_container.default.hex}}",
    "{{colors.tertiary.default.hex}}", "{{colors.on_tertiary.default.hex}}",
    "{{colors.tertiary_container.default.hex}}", "{{colors.on_tertiary_container.default.hex}}",
    "{{colors.surface.default.hex}}", "{{colors.on_surface.default.hex}}",
    "{{colors.surface_variant.default.hex}}", "{{colors.on_surface_variant.default.hex}}",
    "{{colors.outline.default.hex}}", "{{colors.outline_variant.default.hex}}",
    "{{colors.error.default.hex}}", "{{colors.on_error.default.hex}}",
    "{{colors.error_container.default.hex}}", "{{colors.on_error_container.default.hex}}",
    
    # --- Hardcoded Palette (Standard & Vibrant) ---
    "#ff0000", "#00ff00", "#0000ff", "#ffffff", "#000000", "#00000000",
    "#ffd700", "#39ff14", "#ff00ff", "#00ffff", "#ffa500", "#800080",
    "#ffc0cb", "#a52a2a", "#808080", "#c0c0c0", 
    
    # --- Hardcoded Palette (Pastel & Atmospheric) ---
    "#1e1e2e", "#f5e0dc", "#f38ba8", "#a6e3a1", 
    "#89b4fa", "#f9e2af", "#cba6f7", "#94e2d5"
]

COLOR_HINTS = [
    # --- Matugen Hints ---
    "Primary", "On Primary", "Primary Container", "On Primary Cont",
    "Secondary", "On Secondary", "Secondary Container", "On Sec Cont",
    "Tertiary", "On Tertiary", "Tertiary Container", "On Ter Cont",
    "Surface", "On Surface", "Surface Variant", "On Surf Var",
    "Outline", "Outline Variant",
    "Error", "On Error", "Error Container", "On Err Cont",
    
    # --- Hardcoded Standard Hints ---
    "Red", "Green", "Blue", "White", "Black", "Transparent",
    "Gold", "Neon Green", "Magenta", "Cyan", "Orange", "Purple",
    "Pink", "Brown", "Gray", "Silver", 
    
    # --- Hardcoded Atmospheric Hints ---
    "Catppuccin Base", "Rosewater", "Pastel Red", "Pastel Green", 
    "Pastel Blue", "Pastel Yellow", "Lavender", "Mint"
]

# Shared Alpha/Opacity instructions for all color fields
ALPHA_HELP = (
    "\n\n**Alpha Opacity Quick Reference:**\n"
    "`1a` = 10% | `33` = 20% | `4d` = 30% | `66` = 40%\n"
    "`80` = 50% | `99` = 60% | `b3` = 70% | `cc` = 80%\n"
    "`e6` = 90% | `ff` = 100%\n\n"
    "Append these to any color variable (e.g., `{{colors.surface.default.hex}}1a`)."
)

# =============================================================================
# 4. TABS DEFINITION
# =============================================================================
TABS = [
    "Layout",
    "Visuals",
    "Behavior",
    "Urgency",
    "Modules",
    "Profiles"
]

# =============================================================================
# 5. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: LAYOUT (Global Geometry & Positioning)
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Layer",
            key="layer",
            scope="DEFAULT",       
            type_="cycle",
            default="overlay",
            options=["background", "bottom", "top", "overlay"],
            group="Geometry",
            extended_help="**Compositor Layer**\n\nArranges notifications at a specific Wayland surface layer. `overlay` ensures notifications display on top of fullscreen windows."
        ),
        ConfigItem(
            label="Anchor",
            key="anchor",
            scope="DEFAULT",       
            type_="cycle",
            default="bottom-left",
            options=["top-right", "top-center", "top-left", "bottom-right", "bottom-center", "bottom-left", "center-right", "center-left", "center"],
            group="Geometry",
            extended_help="**Screen Anchor**\n\nDefines exactly where the notification stack grows from on your physical display."
        ),
        ConfigItem(
            label="Width",
            key="width",
            scope="DEFAULT",       
            type_="int",
            default=340,
            min_val=100,
            max_val=800,
            step=10,
            group="Geometry",
            extended_help="**Notification Width**\n\nGlobal maximum width of individual notification popups in pixels."
        ),
        ConfigItem(
            label="Height",
            key="height",
            scope="DEFAULT",       
            type_="int",
            default=150,
            min_val=50,
            max_val=500,
            step=10,
            group="Geometry",
            extended_help="**Maximum Height**\n\nGlobal maximum height of an individual notification before clipping occurs."
        ),
        ConfigItem(
            label="Outer",
            key="outer-margin",
            scope="DEFAULT",       
            type_="string",
            default="0,0,30,0",
            group="Spacing",
            extended_help="**Outer Screen Margin**\n\nCSS-style margins (Top, Right, Bottom, Left) that push the entire notification stack away from the edges of your screen."
        ),
        ConfigItem(
            label="Margin",
            key="margin",
            scope="DEFAULT",       
            type_="string",
            default="5",
            group="Spacing",
            extended_help="**Gap Margin**\n\nThe gap spacing inserted between individual notifications in the stack."
        ),
        ConfigItem(
            label="Padding",
            key="padding",
            scope="DEFAULT",       
            type_="string",
            default="10",
            group="Spacing",
            extended_help="**Inner Padding**\n\nCSS-style padding separating the internal text/icons from the notification border."
        ),
        ConfigItem(
            label="Radius",
            key="border-radius",
            scope="DEFAULT",       
            type_="int",
            default=18,
            min_val=0,
            max_val=50,
            step=1,
            group="Borders",
            extended_help="**Corner Radius**\n\nSmooths the edges of the popup window. Set to 0 for strict rectangles."
        ),
        ConfigItem(
            label="Size",
            key="border-size",
            scope="DEFAULT",       
            type_="int",
            default=1,
            min_val=0,
            max_val=20,
            step=1,
            group="Borders",
            extended_help="**Border Thickness**\n\nThe pixel thickness of the outer colored stroke surrounding the notification."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: VISUALS (Aesthetics & Typography)
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Font",
            key="font",
            scope="DEFAULT",       
            type_="string",
            default="monospace 10",
            group="Text",
            extended_help="**Typography Definition**\n\nDeclares the Pango font family and size used to render incoming notification text."
        ),
        ConfigItem(
            label="Markup",
            key="markup",
            scope="DEFAULT",       
            type_="bool",
            default=True,
            group="Text",
            extended_help="**Pango Markup**\n\nAllows notifications to render HTML-like bold (<b>), italic (<i>), and color tags."
        ),
        ConfigItem(
            label="Format",
            key="format",
            scope="DEFAULT",       
            type_="string",
            default="<b>%s</b>\\n%b",
            group="Text",
            extended_help="**Layout Formatting**\n\nDefines how the Summary (`%s`) and Body (`%b`) are arranged. `\\n` represents a line break."
        ),
        ConfigItem(
            label="Enable",
            key="icons",
            scope="DEFAULT",       
            type_="bool",
            default=True,
            group="Icons",
            extended_help="**Toggle Icons**\n\nMaster switch for permitting application icons and artwork to render inside notifications."
        ),
        ConfigItem(
            label="MaxSize",
            key="max-icon-size",
            scope="DEFAULT",       
            type_="int",
            default=30,
            min_val=16,
            max_val=128,
            step=4,
            group="Icons",
            extended_help="**Image Scaling Limit**\n\nLimits the rendering dimensions of incoming album art or app logos."
        ),
        ConfigItem(
            label="Radius",
            key="icon-border-radius",
            scope="DEFAULT",       
            type_="int",
            default=8,
            min_val=0,
            max_val=32,
            step=1,
            group="Icons",
            extended_help="**Icon Corner Smoothing**\n\nApplies rounded corners specifically to the internal icon/image."
        ),
        ConfigItem(
            label="Background",
            key="background-color",
            scope="DEFAULT",       
            type_="color",
            default="{{colors.surface.default.hex}}1a",
            options=COLOR_OPTIONS,
            hints=COLOR_HINTS,
            group="Colors",
            extended_help="**Global Base Color**\n\nThe overarching fill color injected beneath all elements." + ALPHA_HELP
        ),
        ConfigItem(
            label="Text",
            key="text-color",
            scope="DEFAULT",       
            type_="color",
            default="{{colors.on_surface.default.hex}}",
            options=COLOR_OPTIONS,
            hints=COLOR_HINTS,
            group="Colors",
            extended_help="**Global Typography Color**\n\nThe overarching text color." + ALPHA_HELP
        ),
        ConfigItem(
            label="Border",
            key="border-color",
            scope="DEFAULT",       
            type_="color",
            default="{{colors.outline.default.hex}}33",
            options=COLOR_OPTIONS,
            hints=COLOR_HINTS,
            group="Colors",
            extended_help="**Global Stroke Color**\n\nThe overarching outline color framing the notification." + ALPHA_HELP
        ),
        ConfigItem(
            label="Progress",
            key="progress-color",
            scope="DEFAULT",       
            type_="color",
            default="{{colors.primary_container.default.hex}}59",
            options=COLOR_OPTIONS,
            hints=COLOR_HINTS,
            group="Colors",
            extended_help="**Progress Bar Fill**\n\nColor utilized for volume or brightness indicator bars inside standard popups." + ALPHA_HELP
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: BEHAVIOR (Timeouts, Sorting, & Stack Limits)
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Timeout",
            key="default-timeout",
            scope="DEFAULT",       
            type_="int",
            default=5000,
            min_val=0,
            max_val=30000,
            step=500,
            group="Timers",
            extended_help="**Lifespan**\n\nMilliseconds a normal notification stays on screen before disappearing."
        ),
        ConfigItem(
            label="Ignore",
            key="ignore-timeout",
            scope="DEFAULT",       
            type_="bool",
            default=False,
            group="Timers",
            extended_help="**Infinite Lifespan Override**\n\nIf ON, completely ignores timeout requests from apps. Notifications must be manually dismissed."
        ),
        ConfigItem(
            label="Visible",
            key="max-visible",
            scope="DEFAULT",       
            type_="int",
            default=6,
            min_val=1,
            max_val=20,
            step=1,
            group="Queue",
            extended_help="**Stack Cap**\n\nThe maximum amount of physical notifications rendered on screen at any given time."
        ),
        ConfigItem(
            label="MaxHistory",
            key="max-history",
            scope="DEFAULT",       
            type_="int",
            default=50,
            min_val=1,
            max_val=200,
            step=5,
            group="Queue",
            extended_help="**Buffer Size**\n\nMaximum amount of dismissed/expired notifications to keep stored in memory for retrieval."
        ),
        ConfigItem(
            label="History",
            key="history",
            scope="DEFAULT",       
            type_="bool",
            default=True,
            group="Queue",
            extended_help="**Archival Enable**\n\nToggle whether expired notifications should be saved to the buffer."
        ),
        ConfigItem(
            label="Sort",
            key="sort",
            scope="DEFAULT",       
            type_="cycle",
            default="-time",
            options=["-time", "+time", "-priority", "+priority"],
            group="Queue",
            extended_help="**Stack Orientation**\n\nDetermines whether new notifications appear at the top or bottom of the stack."
        ),
        # --- ACTIONS HYBRID FOLDER ---
        ConfigItem(
            label="Actions",
            key="actions",
            scope="DEFAULT",       
            type_="bool",
            default=True,
            is_parent=True,
            expanded=False,
            group="Clicks",
            extended_help="**Allow Triggers**\n\nMaster toggle for allowing notifications to execute commands when clicked. Expand this folder to bind specific shell/app commands to your mouse buttons."
        ),
        ConfigItem(
            label="LeftBtn",
            key="on-button-left",
            scope="DEFAULT",       
            type_="string",
            default="invoke-default-action",
            parent_ref="actions",
            extended_help="**Primary Click Action**\n\nWhat happens when you left-click a notification. Usually `invoke-default-action` opens the calling app."
        ),
        ConfigItem(
            label="MidBtn",
            key="on-button-middle",
            scope="DEFAULT",       
            type_="string",
            default="exec makoctl menu -n \"$MAKO_NOTIFICATION_ID\" -- rofi -dmenu -p Action:",
            parent_ref="actions",
            extended_help="**Middle Click Action**\n\nSpawns a Rofi menu listing all secondary actions embedded in the notification."
        ),
        ConfigItem(
            label="RightBtn",
            key="on-button-right",
            scope="DEFAULT",       
            type_="string",
            default="dismiss",
            parent_ref="actions",
            extended_help="**Secondary Click Action**\n\nUsually immediately clears the notification from the screen (`dismiss`)."
        ),
        ConfigItem(
            label="OnNotify",
            key="on-notify",
            scope="DEFAULT",       
            type_="string",
            default="exec pkill -RTMIN+8 waybar",
            group="Triggers",
            extended_help="**Daemon Hook**\n\nA shell command fired universally the exact instant a notification is generated."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: URGENCY (Criteria-based Overrides via Submenus)
    # -------------------------------------------------------------------------
    3: [
        # --- LOW URGENCY ---
        ConfigItem(
            label="Low", key="menu_low", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Levels",
            extended_help="**Low Urgency Overrides**\n\nAdjustments for trivial system alerts."
        ),
        ConfigItem(
            label="Timeout", key="default-timeout", scope="urgency=low", type_="int", default=2000, min_val=0, max_val=15000, step=500, parent_ref="menu_low",
            extended_help="**Low Urgency Timeout**\n\nOverrides default lifespan for trivial alerts. Lowering this clears clutter faster."
        ),
        
        # --- NORMAL URGENCY ---
        ConfigItem(
            label="Normal", key="menu_norm", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Levels",
            extended_help="**Normal Urgency Overrides**\n\nAdjustments for standard app alerts."
        ),
        ConfigItem(
            label="Timeout", key="default-timeout", scope="urgency=normal", type_="int", default=3000, min_val=0, max_val=15000, step=500, parent_ref="menu_norm",
            extended_help="**Normal Urgency Timeout**\n\nOverrides default lifespan for standard items."
        ),

        # --- CRITICAL URGENCY ---
        ConfigItem(
            label="Critical", key="menu_crit", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Levels",
            extended_help="**Critical Urgency Overrides**\n\nStrict behavior overrides for highly important system failures or battery warnings."
        ),
        ConfigItem(
            label="Invisible", key="invisible", scope="urgency=critical", type_="bool", default=False, parent_ref="menu_crit",
            extended_help="**Critical Visibility**\n\nEnsures critical alerts forcefully bypass any Do Not Disturb invisibility rules."
        ),
        ConfigItem(
            label="Timeout", key="default-timeout", scope="urgency=critical", type_="int", default=0, min_val=0, max_val=30000, step=500, parent_ref="menu_crit",
            extended_help="**Critical Timeout**\n\nSetting to 0 ensures critical items stay on screen infinitely until manually cleared."
        ),
        ConfigItem(
            label="Ignore", key="ignore-timeout", scope="urgency=critical", type_="bool", default=True, parent_ref="menu_crit",
            extended_help="**Force Display**\n\nForces Mako to ignore timeout instructions from the app sending the critical error."
        ),
        ConfigItem(
            label="OnNotify", key="on-notify", scope="urgency=critical", type_="string", default="exec pkill -RTMIN+8 waybar", parent_ref="menu_crit",
            extended_help="**Critical Event Hook**\n\nA shell script executed exclusively when a critical priority alert fires."
        ),
        ConfigItem(
            label="Background", key="background-color", scope="urgency=critical", type_="color", default="{{colors.error_container.default.hex}}e6", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_crit",
            extended_help="**Critical Background**\n\nUsually set to red/error colors." + ALPHA_HELP
        ),
        ConfigItem(
            label="Text", key="text-color", scope="urgency=critical", type_="color", default="{{colors.on_error_container.default.hex}}", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_crit",
            extended_help="**Critical Text**\n\nTypography color for critical alerts." + ALPHA_HELP
        ),
        ConfigItem(
            label="Border", key="border-color", scope="urgency=critical", type_="color", default="{{colors.error.default.hex}}", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_crit",
            extended_help="**Critical Border**\n\nBorder frame color for critical alerts." + ALPHA_HELP
        ),

        # --- MODES ---
        ConfigItem(
            label="DND", key="menu_dnd", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Modes",
            extended_help="**Do Not Disturb Mode**\n\nSettings applied when `makoctl mode -s do-not-disturb` is activated."
        ),
        ConfigItem(
            label="Invisible", key="invisible", scope="mode=do-not-disturb", type_="bool", default=True, parent_ref="menu_dnd",
            extended_help="**Hide All**\n\nHides all standard popups silently, caching them directly in history instead."
        ),
        ConfigItem(
            label="OnNotify", key="on-notify", scope="mode=do-not-disturb", type_="string", default="none", parent_ref="menu_dnd",
            extended_help="**Mute Hooks**\n\nPrevents external notification chimes or scripts from executing while DND is active."
        ),

        ConfigItem(
            label="Silent", key="menu_silent", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Modes",
            extended_help="**Silent Mode**\n\nDisplays notifications normally, but blocks audio hooks."
        ),
        ConfigItem(
            label="OnNotify", key="on-notify", scope="mode=silent", type_="string", default="none", parent_ref="menu_silent",
            extended_help="**Nullify Audio**\n\nOverrides the `on-notify` hook to block standard sound executions."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: MODULES (Dusky App Specific Configurations)
    # -------------------------------------------------------------------------
    4: [
        # =====================================================================
        # GROUP: APPS (Specific application overrides)
        # =====================================================================
        ConfigItem(
            label="Spotify", key="menu_spotify", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Apps",
            extended_help="**Spotify Integration**\n\nConfigure how notifications from the Spotify desktop client are handled."
        ),
        ConfigItem(
            label="Invisible", key="invisible", scope="app-name=Spotify", type_="bool", default=True, parent_ref="menu_spotify",
            extended_help="**Spotify Silencer**\n\nWhen ON, this hides track-change popups, dropping them directly into the history buffer."
        ),
        
        ConfigItem(
            label="VLC", key="menu_vlc", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Apps",
            extended_help="**VLC Media Player**\n\nSettings specific to VLC's media overlays."
        ),
        ConfigItem(
            label="Timeout", key="default-timeout", scope='app-name="VLC media player"', type_="int", default=1500, min_val=0, max_val=10000, step=500, parent_ref="menu_vlc",
            extended_help="**VLC Display Time**\n\nDuration in milliseconds for VLC specific track/volume popups."
        ),
        
        ConfigItem(
            label="Grimblast", key="menu_grimblast", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Apps",
            extended_help="**Grimblast Utility**\n\nDefines the behavior of the notification that appears after taking a screenshot."
        ),
        ConfigItem(label="Size", key="max-icon-size", scope="app-name=grimblast", type_="int", default=30, min_val=16, max_val=200, step=4, parent_ref="menu_grimblast", extended_help="**Screenshot Thumbnail Size**\n\nThe maximum pixel dimensions for the captured screenshot preview image."),
        ConfigItem(label="Timeout", key="default-timeout", scope="app-name=grimblast", type_="int", default=4000, min_val=0, max_val=10000, step=500, parent_ref="menu_grimblast", extended_help="**Screenshot Alert Lifespan**\n\nMilliseconds the screenshot confirmation popup stays on screen."),
        ConfigItem(label="Format", key="format", scope="app-name=grimblast", type_="string", default="<b>%s</b>\\n%b", parent_ref="menu_grimblast", extended_help="**Screenshot Text Format**\n\nDefines the layout logic for the Grimblast notification text output."),
        ConfigItem(label="OnClick", key="on-button-left", scope="app-name=grimblast", type_="string", default="exec imv \"$MAKO_NOTIFICATION_BODY\"", parent_ref="menu_grimblast", extended_help="**View Image Action**\n\nExecutes this command when you left-click the alert. Defaults to opening the image directly in `imv`."),

        ConfigItem(
            label="Updater", key="menu_updater", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Apps",
            extended_help="**Dusky Updater**\n\nHandles update alerts spawned by the system dotfile synchronization scripts."
        ),
        ConfigItem(label="Layer", key="layer", scope='summary="Dusky Dotfiles"', type_="cycle", default="overlay", options=["background", "bottom", "top", "overlay"], parent_ref="menu_updater", extended_help="**Updater Layer**\n\nArranges the update notification at a specific Wayland surface layer."),
        ConfigItem(label="OnClick", key="on-button-left", scope='summary="Dusky Dotfiles"', type_="string", default="exec kitty --class update_dusky.sh --hold ~/user_scripts/update_dusky/update_dusky.sh", parent_ref="menu_updater", extended_help="**Trigger Update**\n\nShell script executed on interaction to launch the update terminal."),

        # =====================================================================
        # GROUP: OSD (On-Screen Display for Volume/Brightness)
        # =====================================================================
        ConfigItem(
            label="OSD", key="menu_osd", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="OSD",
            extended_help="**On-Screen Display**\n\nControls the popup aesthetic for hardware changes like Volume or Brightness. These specifically target notifications pushed with `app-name=OSD`."
        ),
        ConfigItem(label="Anchor", key="anchor", scope="app-name=OSD", type_="cycle", default="bottom-center", options=["top-right", "top-center", "top-left", "bottom-right", "bottom-center", "bottom-left", "center-right", "center-left", "center"], parent_ref="menu_osd", extended_help="**OSD Position**\n\nWhere the Volume/Brightness overlay anchors on the display."),
        ConfigItem(label="Layer", key="layer", scope="app-name=OSD", type_="cycle", default="overlay", options=["background", "bottom", "top", "overlay"], parent_ref="menu_osd", extended_help="**OSD Layer**\n\nArranges the hardware popup at a specific Wayland surface layer."),
        ConfigItem(label="Width", key="width", scope="app-name=OSD", type_="int", default=242, min_val=50, max_val=800, step=2, parent_ref="menu_osd", extended_help="**OSD Pixel Width**\n\nTotal width allocated for the progress bar and icon."),
        ConfigItem(label="Height", key="height", scope="app-name=OSD", type_="int", default=48, min_val=10, max_val=200, step=2, parent_ref="menu_osd", extended_help="**OSD Pixel Height**\n\nTotal thickness of the hardware overlay."),
        ConfigItem(label="Outer", key="outer-margin", scope="app-name=OSD", type_="string", default="0,0,0,0", parent_ref="menu_osd", extended_help="**OSD Offset Margin**\n\nPushes the OSD inward from the screen edge."),
        ConfigItem(label="Margin", key="margin", scope="app-name=OSD", type_="string", default="0", parent_ref="menu_osd", extended_help="**OSD Stack Gap**\n\nGap generated if multiple OSD notifications stack up (rare)."),
        ConfigItem(label="Padding", key="padding", scope="app-name=OSD", type_="string", default="0", parent_ref="menu_osd", extended_help="**OSD Internal Padding**\n\nSpace between the progress bar and the external popup border."),
        ConfigItem(label="Radius", key="border-radius", scope="app-name=OSD", type_="int", default=24, min_val=0, max_val=50, step=1, parent_ref="menu_osd", extended_help="**OSD Rounding**\n\nCorner radius specifically for the hardware popup."),
        ConfigItem(label="Size", key="border-size", scope="app-name=OSD", type_="int", default=0, min_val=0, max_val=10, step=1, parent_ref="menu_osd", extended_help="**OSD Border Stroke**\n\nThickness of the outer border ring."),
        ConfigItem(label="Icons", key="icons", scope="app-name=OSD", type_="bool", default=True, parent_ref="menu_osd", extended_help="**OSD Icon Toggle**\n\nDetermines whether the volume speaker or sun icon renders."),
        ConfigItem(label="Align", key="text-alignment", scope="app-name=OSD", type_="cycle", default="center", options=["left", "center", "right"], parent_ref="menu_osd", extended_help="**OSD Alignment**\n\nJustification of text elements inside the OSD widget."),
        ConfigItem(label="Font", key="font", scope="app-name=OSD", type_="string", default="monospace 10", parent_ref="menu_osd", extended_help="**OSD Font Override**\n\nCustom typography solely for the hardware OSD popup."),
        ConfigItem(label="Timeout", key="default-timeout", scope="app-name=OSD", type_="int", default=1000, min_val=0, max_val=10000, step=100, parent_ref="menu_osd", extended_help="**OSD Lifespan**\n\nHow rapidly the widget fades after scrolling the mouse wheel or hitting a media key."),
        ConfigItem(label="OnClick", key="on-button-left", scope="app-name=OSD", type_="string", default="invoke-default-action", parent_ref="menu_osd", extended_help="**OSD Left Click**\n\nAction executed if the user physically clicks the hardware OSD popup."),
        ConfigItem(label="Background", key="background-color", scope="app-name=OSD", type_="color", default="#00000000", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_osd", extended_help="**OSD Fill**\n\nBase color of the widget (Often transparent)." + ALPHA_HELP),
        ConfigItem(label="Text", key="text-color", scope="app-name=OSD", type_="color", default="{{colors.on_surface.default.hex}}ff", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_osd", extended_help="**OSD Typography**\n\nColor for internal text values." + ALPHA_HELP),
        ConfigItem(label="Border", key="border-color", scope="app-name=OSD", type_="color", default="{{colors.outline.default.hex}}33", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_osd", extended_help="**OSD Border Color**\n\nStroke color wrapped around the hardware widget." + ALPHA_HELP),

        # =====================================================================
        # GROUP: KEYS (Keyboard layout popup)
        # =====================================================================
        ConfigItem(
            label="Keys", key="menu_keys", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Keys",
            extended_help="**Keyboard Layout Display**\n\nThe quick popup indicating language or keyboard layout swaps. Targets notifications pushed with `app-name=dusky-keys`."
        ),
        ConfigItem(label="Anchor", key="anchor", scope="app-name=dusky-keys", type_="cycle", default="bottom-center", options=["top-right", "top-center", "top-left", "bottom-right", "bottom-center", "bottom-left", "center-right", "center-left", "center"], parent_ref="menu_keys", extended_help="**Keys Position**\n\nScreen anchor for the layout notification."),
        ConfigItem(label="Layer", key="layer", scope="app-name=dusky-keys", type_="cycle", default="overlay", options=["background", "bottom", "top", "overlay"], parent_ref="menu_keys", extended_help="**Keys Layer**\n\nArranges the layout indicator at a specific Wayland surface layer."),
        ConfigItem(label="Width", key="width", scope="app-name=dusky-keys", type_="int", default=200, min_val=50, max_val=800, step=10, parent_ref="menu_keys", extended_help="**Keys Width**\n\nWidth of the layout text box."),
        ConfigItem(label="Height", key="height", scope="app-name=dusky-keys", type_="int", default=40, min_val=10, max_val=200, step=2, parent_ref="menu_keys", extended_help="**Keys Height**\n\nHeight of the layout text box."),
        ConfigItem(label="Margin", key="margin", scope="app-name=dusky-keys", type_="string", default="0,0,0,0", parent_ref="menu_keys", extended_help="**Keys Margin**\n\nMargin parameters controlling placement offset."),
        ConfigItem(label="Padding", key="padding", scope="app-name=dusky-keys", type_="string", default="0", parent_ref="menu_keys", extended_help="**Keys Padding**\n\nInternal spacing inside the layout text box."),
        ConfigItem(label="Size", key="border-size", scope="app-name=dusky-keys", type_="int", default=0, min_val=0, max_val=10, step=1, parent_ref="menu_keys", extended_help="**Keys Border Thickness**\n\nStroke thickness for the popup."),
        ConfigItem(label="Radius", key="border-radius", scope="app-name=dusky-keys", type_="int", default=20, min_val=0, max_val=50, step=1, parent_ref="menu_keys", extended_help="**Keys Corner Smoothing**\n\nRounding applied to the layout indicator."),
        ConfigItem(label="Icons", key="icons", scope="app-name=dusky-keys", type_="bool", default=False, parent_ref="menu_keys", extended_help="**Keys Icons**\n\nToggles display of keyboard/language icons."),
        ConfigItem(label="Align", key="text-alignment", scope="app-name=dusky-keys", type_="cycle", default="center", options=["left", "center", "right"], parent_ref="menu_keys", extended_help="**Keys Text Alignment**\n\nJustification of the layout string."),
        ConfigItem(label="Font", key="font", scope="app-name=dusky-keys", type_="string", default="monospace 14", parent_ref="menu_keys", extended_help="**Keys Font Override**\n\nCustom typography solely for this specific popup."),
        ConfigItem(label="Format", key="format", scope="app-name=dusky-keys", type_="string", default="%s", parent_ref="menu_keys", extended_help="**Keys Text Format**\n\nStructures the text payload (e.g., %s = summary only)."),
        ConfigItem(label="Timeout", key="default-timeout", scope="app-name=dusky-keys", type_="int", default=1500, min_val=0, max_val=10000, step=100, parent_ref="menu_keys", extended_help="**Keys Lifespan**\n\nHow rapidly the layout popup clears."),
        ConfigItem(label="Background", key="background-color", scope="app-name=dusky-keys", type_="color", default="{{colors.surface.default.hex}}66", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_keys", extended_help="**Keys Background Fill**" + ALPHA_HELP),
        ConfigItem(label="Text", key="text-color", scope="app-name=dusky-keys", type_="color", default="{{colors.on_surface.default.hex}}", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_keys", extended_help="**Keys Text Color**" + ALPHA_HELP),
        ConfigItem(label="Border", key="border-color", scope="app-name=dusky-keys", type_="color", default="{{colors.outline_variant.default.hex}}66", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_keys", extended_help="**Keys Border Color**" + ALPHA_HELP),

        # =====================================================================
        # GROUP: CAVA (Audio Visualizer Applets)
        # =====================================================================
        ConfigItem(
            label="Cava", key="menu_cava", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Cava",
            extended_help="**Audio Visualizer Block**\n\nThe primary animated audio visualizer popup block. Configures notifications pushed with `app-name=dusky-cava`."
        ),
        ConfigItem(label="Anchor", key="anchor", scope="app-name=dusky-cava", type_="cycle", default="bottom-center", options=["top-right", "top-center", "top-left", "bottom-right", "bottom-center", "bottom-left", "center-right", "center-left", "center"], parent_ref="menu_cava", extended_help="**Cava Position**\n\nAnchoring target for the visualizer block."),
        ConfigItem(label="Layer", key="layer", scope="app-name=dusky-cava", type_="cycle", default="overlay", options=["background", "bottom", "top", "overlay"], parent_ref="menu_cava", extended_help="**Cava Layer**\n\nArranges the visualizer block at a specific Wayland surface layer."),
        ConfigItem(label="Width", key="width", scope="app-name=dusky-cava", type_="int", default=380, min_val=100, max_val=800, step=10, parent_ref="menu_cava", extended_help="**Cava Box Width**\n\nHorizontal size of the visualizer spectrum box."),
        ConfigItem(label="Height", key="height", scope="app-name=dusky-cava", type_="int", default=40, min_val=10, max_val=200, step=2, parent_ref="menu_cava", extended_help="**Cava Box Height**\n\nVertical limit for the audio spectrum output."),
        ConfigItem(label="Margin", key="margin", scope="app-name=dusky-cava", type_="string", default="0,0,20,0", parent_ref="menu_cava", extended_help="**Cava Offset Margin**\n\nSpacing pushing the visualizer away from the screen boundaries."),
        ConfigItem(label="Padding", key="padding", scope="app-name=dusky-cava", type_="string", default="0", parent_ref="menu_cava", extended_help="**Cava Internal Padding**\n\nSpace between the animation bars and the bounding box."),
        ConfigItem(label="Size", key="border-size", scope="app-name=dusky-cava", type_="int", default=2, min_val=0, max_val=10, step=1, parent_ref="menu_cava", extended_help="**Cava Border Thickness**\n\nThickness of the framing surrounding the visualization."),
        ConfigItem(label="Radius", key="border-radius", scope="app-name=dusky-cava", type_="int", default=20, min_val=0, max_val=50, step=1, parent_ref="menu_cava", extended_help="**Cava Corner Smoothing**\n\nApplies rounded arcs to the visualizer box."),
        ConfigItem(label="Icons", key="icons", scope="app-name=dusky-cava", type_="bool", default=False, parent_ref="menu_cava", extended_help="**Cava Icon Block**\n\nUsually set to OFF to prevent icons from disrupting the spectrum text formatting."),
        ConfigItem(label="Align", key="text-alignment", scope="app-name=dusky-cava", type_="cycle", default="center", options=["left", "center", "right"], parent_ref="menu_cava", extended_help="**Cava Alignment**\n\nCenters the bouncing visualizer text blocks."),
        ConfigItem(label="Font", key="font", scope="app-name=dusky-cava", type_="string", default="monospace 22", parent_ref="menu_cava", extended_help="**Cava Block Font**\n\nSpecifically scales the font used for the block characters simulating the spectrum."),
        ConfigItem(label="Format", key="format", scope="app-name=dusky-cava", type_="string", default="%s", parent_ref="menu_cava", extended_help="**Cava Payload**\n\nDetermines exactly what data the visualizer displays."),
        ConfigItem(label="Timeout", key="default-timeout", scope="app-name=dusky-cava", type_="int", default=0, min_val=0, max_val=10000, step=100, parent_ref="menu_cava", extended_help="**Cava Refresh Sync**\n\nUsually 0, dictating that the script handles the timeout frames."),
        ConfigItem(label="Background", key="background-color", scope="app-name=dusky-cava", type_="color", default="{{colors.surface_container.default.hex}}ff", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_cava", extended_help="**Cava Fill Color**" + ALPHA_HELP),
        ConfigItem(label="Text", key="text-color", scope="app-name=dusky-cava", type_="color", default="{{colors.primary.default.hex}}", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_cava", extended_help="**Cava Animation Color**\n\nThe color of the active spectrum bars." + ALPHA_HELP),
        ConfigItem(label="Border", key="border-color", scope="app-name=dusky-cava", type_="color", default="{{colors.primary_container.default.hex}}", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_cava", extended_help="**Cava Border Color**" + ALPHA_HELP),

        ConfigItem(
            label="Alert", key="menu_cava_alert", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Cava",
            extended_help="**Cava System Prompts**\n\nNotification alerts originating directly from the visualizer scripts (e.g. Script errors or toggle confirmations)."
        ),
        ConfigItem(label="Anchor", key="anchor", scope="app-name=dusky-cava-alert", type_="cycle", default="bottom-center", options=["top-right", "top-center", "top-left", "bottom-right", "bottom-center", "bottom-left", "center-right", "center-left", "center"], parent_ref="menu_cava_alert", extended_help="**Cava Alert Position**\n\nWhere visualizer error/info popups display."),
        ConfigItem(label="Layer", key="layer", scope="app-name=dusky-cava-alert", type_="cycle", default="overlay", options=["background", "bottom", "top", "overlay"], parent_ref="menu_cava_alert", extended_help="**Cava Alert Layer**\n\nArranges the system prompt at a specific Wayland surface layer."),
        ConfigItem(label="Width", key="width", scope="app-name=dusky-cava-alert", type_="int", default=300, min_val=50, max_val=800, step=10, parent_ref="menu_cava_alert", extended_help="**Cava Alert Width**\n\nWidth of the alert text box."),
        ConfigItem(label="Height", key="height", scope="app-name=dusky-cava-alert", type_="int", default=40, min_val=10, max_val=200, step=2, parent_ref="menu_cava_alert", extended_help="**Cava Alert Height**\n\nVertical limit for the alert box."),
        ConfigItem(label="Margin", key="margin", scope="app-name=dusky-cava-alert", type_="string", default="0,0,20,0", parent_ref="menu_cava_alert", extended_help="**Cava Alert Margin**\n\nScreen offset parameters."),
        ConfigItem(label="Padding", key="padding", scope="app-name=dusky-cava-alert", type_="string", default="0", parent_ref="menu_cava_alert", extended_help="**Cava Alert Padding**\n\nInternal spacing for alert text."),
        ConfigItem(label="Radius", key="border-radius", scope="app-name=dusky-cava-alert", type_="int", default=20, min_val=0, max_val=50, step=1, parent_ref="menu_cava_alert", extended_help="**Cava Alert Smoothing**\n\nRounded corners for the alert box."),
        ConfigItem(label="Align", key="text-alignment", scope="app-name=dusky-cava-alert", type_="cycle", default="center", options=["left", "center", "right"], parent_ref="menu_cava_alert", extended_help="**Cava Alert Justification**\n\nCenters the prompt text."),
        ConfigItem(label="Font", key="font", scope="app-name=dusky-cava-alert", type_="string", default="monospace 12", parent_ref="menu_cava_alert", extended_help="**Cava Alert Font**\n\nTypography settings for prompt text."),
        ConfigItem(label="Timeout", key="default-timeout", scope="app-name=dusky-cava-alert", type_="int", default=3000, min_val=0, max_val=10000, step=100, parent_ref="menu_cava_alert", extended_help="**Cava Alert Lifespan**\n\nHow long informational prompts stay visible."),
        ConfigItem(label="Background", key="background-color", scope="app-name=dusky-cava-alert", type_="color", default="{{colors.tertiary_container.default.hex}}ff", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_cava_alert", extended_help="**Cava Alert Fill**" + ALPHA_HELP),
        ConfigItem(label="Text", key="text-color", scope="app-name=dusky-cava-alert", type_="color", default="{{colors.on_tertiary_container.default.hex}}", options=COLOR_OPTIONS, hints=COLOR_HINTS, parent_ref="menu_cava_alert", extended_help="**Cava Alert Text Color**" + ALPHA_HELP),
    ],


    # -------------------------------------------------------------------------
    # TAB 5: PROFILES (Advanced Controls & State Synchronization)
    # -------------------------------------------------------------------------
    5: [
        ConfigItem(
            label="TestNormal",
            key="action_test_normal",
            scope="DEFAULT",
            type_="action",
            default="notify-send 'Test Alert' 'This is a standard notification.'",
            group="Execution",
            extended_help="**Standard Test**\n\nFires a normal notification to preview your global geometry and timeout settings."
        ),
        ConfigItem(
            label="TestIcon",
            key="action_test_icon",
            scope="DEFAULT",
            type_="action",
            default="notify-send -i 'dialog-information' 'Icon Alert' 'This notification includes a standard system icon.'",
            group="Execution",
            extended_help="**Icon Test**\n\nPreviews your icon border-radius and maximum size settings."
        ),
        ConfigItem(
            label="TestCritical",
            key="action_test_critical",
            scope="DEFAULT",
            type_="action",
            default="notify-send -u critical -i 'dialog-error' 'Critical Failure' 'This is a critical alert. It should bypass DND and stay on screen indefinitely.'",
            group="Execution",
            extended_help="**Critical Test**\n\nFires an urgent notification to preview critical urgency overrides."
        ),
        ConfigItem(
            label="TestOSD",
            key="action_test_osd",
            scope="DEFAULT",
            type_="action",
            default="notify-send -a OSD -i 'audio-volume-high' 'Volume: 75%' -h int:value:75",
            group="Execution",
            extended_help="**OSD Popup Test**\n\nSimulates a hardware volume change to preview your OSD module aesthetics."
        ),
        ConfigItem(
            label="Regenerate",
            key="action_reload_mako", 
            scope="DEFAULT",          
            type_="action",
            default="bash -c '~/user_scripts/theme_matugen/theme_ctl.sh refresh && makoctl reload'",
            group="Execution",
            extended_help="**Live Theme Regeneration**\n\nExecutes `theme_ctl.sh refresh` to compile all templated parameters and immediately reloads the active `makoctl` daemon to apply visual changes."
        ),
        ConfigItem(
            label="Reset",
            key="preset_factory_reset",
            scope="DEFAULT",          
            type_="preset",
            default=None,
            group="Defaults",
            preset_payload={
                "__ALL_DEFAULTS__": True
            },
            extended_help="**Nuclear Reset**\n\nReverts every single configuration item across all tabs directly back to its originally programmed Matugen template state. Click Regenerate afterward to apply."
        ),
    ]
}
