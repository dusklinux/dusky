"""Linux evdev key codes, classification, and US-layout character maps.

Canonical codes: include/uapi/linux/input-event-codes.h
Character resolution is US QWERTY plus the ISO 102nd key and the
keypad (NumLock-aware). No layout daemon, no XKB, no Wayland.
"""

from enum import StrEnum

# ---------------------------------------------------------------------------
# Event / LED codes (linux/input-event-codes.h)
# ---------------------------------------------------------------------------
EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
EV_MSC = 0x04
EV_LED = 0x11

SYN_REPORT = 0x00
SYN_CONFIG = 0x01
SYN_MT_REPORT = 0x02
SYN_DROPPED = 0x03

LED_NUML = 0x00
LED_CAPSL = 0x01
LED_SCROLLL = 0x02

KEY_MAX = 0x2FF

# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
KEY_ESC = 1
KEY_1 = 2
KEY_2 = 3
KEY_3 = 4
KEY_4 = 5
KEY_5 = 6
KEY_6 = 7
KEY_7 = 8
KEY_8 = 9
KEY_9 = 10
KEY_0 = 11
KEY_MINUS = 12
KEY_EQUAL = 13
KEY_BACKSPACE = 14
KEY_TAB = 15
KEY_Q = 16
KEY_W = 17
KEY_E = 18
KEY_R = 19
KEY_T = 20
KEY_Y = 21
KEY_U = 22
KEY_I = 23
KEY_O = 24
KEY_P = 25
KEY_LEFTBRACE = 26
KEY_RIGHTBRACE = 27
KEY_ENTER = 28
KEY_LEFTCTRL = 29
KEY_A = 30
KEY_S = 31
KEY_D = 32
KEY_F = 33
KEY_G = 34
KEY_H = 35
KEY_J = 36
KEY_K = 37
KEY_L = 38
KEY_SEMICOLON = 39
KEY_APOSTROPHE = 40
KEY_GRAVE = 41
KEY_LEFTSHIFT = 42
KEY_BACKSLASH = 43
KEY_Z = 44
KEY_X = 45
KEY_C = 46
KEY_V = 47
KEY_B = 48
KEY_N = 49
KEY_M = 50
KEY_COMMA = 51
KEY_DOT = 52
KEY_SLASH = 53
KEY_RIGHTSHIFT = 54
KEY_KPASTERISK = 55
KEY_LEFTALT = 56
KEY_SPACE = 57
KEY_CAPSLOCK = 58
KEY_F1 = 59
KEY_F2 = 60
KEY_F3 = 61
KEY_F4 = 62
KEY_F5 = 63
KEY_F6 = 64
KEY_F7 = 65
KEY_F8 = 66
KEY_F9 = 67
KEY_F10 = 68
KEY_NUMLOCK = 69
KEY_SCROLLLOCK = 70
KEY_KP7 = 71
KEY_KP8 = 72
KEY_KP9 = 73
KEY_KPMINUS = 74
KEY_KP4 = 75
KEY_KP5 = 76
KEY_KP6 = 77
KEY_KPPLUS = 78
KEY_KP1 = 79
KEY_KP2 = 80
KEY_KP3 = 81
KEY_KP0 = 82
KEY_KPDOT = 83
KEY_ZENKAKUHANKAKU = 85
KEY_102ND = 86
KEY_F11 = 87
KEY_F12 = 88
KEY_RO = 89
KEY_KATAKANA = 90
KEY_HIRAGANA = 91
KEY_HENKAN = 92
KEY_KATAKANAHIRAGANA = 93
KEY_MUHENKAN = 94
KEY_KPJPCOMMA = 95
KEY_KPENTER = 96
KEY_RIGHTCTRL = 97
KEY_KPSLASH = 98
KEY_SYSRQ = 99
KEY_RIGHTALT = 100
KEY_LINEFEED = 101
KEY_HOME = 102
KEY_UP = 103
KEY_PAGEUP = 104
KEY_LEFT = 105
KEY_RIGHT = 106
KEY_END = 107
KEY_DOWN = 108
KEY_PAGEDOWN = 109
KEY_INSERT = 110
KEY_DELETE = 111
KEY_MACRO = 112
KEY_MUTE = 113
KEY_VOLUMEDOWN = 114
KEY_VOLUMEUP = 115
KEY_POWER = 116
KEY_KPEQUAL = 117
KEY_KPPLUSMINUS = 118
KEY_PAUSE = 119
KEY_SCALE = 120
KEY_KPCOMMA = 121
KEY_HANGEUL = 122
KEY_HANJA = 123
KEY_YEN = 124
KEY_LEFTMETA = 125
KEY_RIGHTMETA = 126
KEY_COMPOSE = 127
KEY_STOP = 128
KEY_AGAIN = 129
KEY_PROPS = 130
KEY_UNDO = 131
KEY_FRONT = 132
KEY_COPY = 133
KEY_OPEN = 134
KEY_PASTE = 135
KEY_FIND = 136
KEY_CUT = 137
KEY_HELP = 138
KEY_MENU = 139
KEY_CALC = 140
KEY_SETUP = 141
KEY_SLEEP = 142
KEY_WAKEUP = 143
KEY_FILE = 144
KEY_SENDFILE = 145
KEY_DELETEFILE = 146
KEY_XFER = 147
KEY_PROG1 = 148
KEY_PROG2 = 149
KEY_WWW = 150
KEY_MSDOS = 151
KEY_COFFEE = 152
KEY_ROTATE_DISPLAY = 153
KEY_CYCLEWINDOWS = 154
KEY_MAIL = 155
KEY_BOOKMARKS = 156
KEY_COMPUTER = 157
KEY_BACK = 158
KEY_FORWARD = 159
KEY_CLOSECD = 160
KEY_EJECTCD = 161
KEY_EJECTCLOSECD = 162
KEY_NEXTSONG = 163
KEY_PLAYPAUSE = 164
KEY_PREVIOUSSONG = 165
KEY_STOPCD = 166
KEY_RECORD = 167
KEY_REWIND = 168
KEY_PHONE = 169
KEY_ISO = 170
KEY_CONFIG = 171
KEY_HOMEPAGE = 172
KEY_REFRESH = 173
KEY_EXIT = 174
KEY_MOVE = 175
KEY_EDIT = 176
KEY_SCROLLUP = 177
KEY_SCROLLDOWN = 178
KEY_KPLEFTPAREN = 179
KEY_KPRIGHTPAREN = 180
KEY_NEW = 181
KEY_REDO = 182
KEY_F13 = 183
KEY_F14 = 184
KEY_F15 = 185
KEY_F16 = 186
KEY_F17 = 187
KEY_F18 = 188
KEY_F19 = 189
KEY_F20 = 190
KEY_F21 = 191
KEY_F22 = 192
KEY_F23 = 193
KEY_F24 = 194
KEY_PLAYCD = 200
KEY_PAUSECD = 201
KEY_PROG3 = 202
KEY_PROG4 = 203
KEY_DASHBOARD = 204
KEY_SUSPEND = 205
KEY_CLOSE = 206
KEY_PLAY = 207
KEY_FASTFORWARD = 208
KEY_BASSBOOST = 209
KEY_PRINT = 210
KEY_HP = 211
KEY_CAMERA = 212
KEY_SOUND = 213
KEY_QUESTION = 214
KEY_EMAIL = 215
KEY_CHAT = 216
KEY_SEARCH = 217
KEY_CONNECT = 218
KEY_FINANCE = 219
KEY_SPORT = 220
KEY_SHOP = 221
KEY_ALTERASE = 222
KEY_CANCEL = 223
KEY_BRIGHTNESSDOWN = 224
KEY_BRIGHTNESSUP = 225
KEY_MEDIA = 226
KEY_SWITCHVIDEOMODE = 227
KEY_KBDILLUMTOGGLE = 228
KEY_KBDILLUMDOWN = 229
KEY_KBDILLUMUP = 230
KEY_SEND = 231
KEY_REPLY = 232
KEY_FORWARDMAIL = 233
KEY_SAVE = 234
KEY_DOCUMENTS = 235
KEY_BATTERY = 236
KEY_BLUETOOTH = 237
KEY_WLAN = 238
KEY_UWB = 239
KEY_UNKNOWN = 240
KEY_VIDEO_NEXT = 241
KEY_VIDEO_PREV = 242
KEY_BRIGHTNESS_CYCLE = 243
KEY_BRIGHTNESS_AUTO = 244
KEY_DISPLAY_OFF = 245
KEY_WWAN = 246
KEY_RFKILL = 247
KEY_MICMUTE = 248

# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------
BACKSPACE_KEYS = frozenset({KEY_BACKSPACE})

SHIFT_KEYS = frozenset({KEY_LEFTSHIFT, KEY_RIGHTSHIFT})

SHORTCUT_MODIFIER_KEYS = frozenset(
    {KEY_LEFTCTRL, KEY_RIGHTCTRL, KEY_LEFTMETA, KEY_RIGHTMETA}
)

MODIFIER_KEYS = frozenset(
    {
        KEY_LEFTCTRL,
        KEY_RIGHTCTRL,
        KEY_LEFTSHIFT,
        KEY_RIGHTSHIFT,
        KEY_LEFTALT,
        KEY_RIGHTALT,
        KEY_LEFTMETA,
        KEY_RIGHTMETA,
        KEY_CAPSLOCK,
        KEY_COMPOSE,
        KEY_NUMLOCK,
        KEY_SCROLLLOCK,
    }
)

NAVIGATION_KEYS = frozenset(
    {
        KEY_HOME,
        KEY_UP,
        KEY_PAGEUP,
        KEY_LEFT,
        KEY_RIGHT,
        KEY_END,
        KEY_DOWN,
        KEY_PAGEDOWN,
        KEY_INSERT,
        KEY_DELETE,
    }
)

FUNCTION_KEYS = frozenset(
    {
        KEY_F1,
        KEY_F2,
        KEY_F3,
        KEY_F4,
        KEY_F5,
        KEY_F6,
        KEY_F7,
        KEY_F8,
        KEY_F9,
        KEY_F10,
        KEY_F11,
        KEY_F12,
        KEY_F13,
        KEY_F14,
        KEY_F15,
        KEY_F16,
        KEY_F17,
        KEY_F18,
        KEY_F19,
        KEY_F20,
        KEY_F21,
        KEY_F22,
        KEY_F23,
        KEY_F24,
    }
)

# Keypad keys that become navigation when NumLock is off.
KEYPAD_NAV_KEYS = frozenset(
    {
        KEY_KP0,
        KEY_KP1,
        KEY_KP2,
        KEY_KP3,
        KEY_KP4,
        KEY_KP5,
        KEY_KP6,
        KEY_KP7,
        KEY_KP8,
        KEY_KP9,
        KEY_KPDOT,
    }
)

KEYPAD_KEYS = KEYPAD_NAV_KEYS | frozenset(
    {
        KEY_KPASTERISK,
        KEY_KPMINUS,
        KEY_KPPLUS,
        KEY_KPENTER,
        KEY_KPSLASH,
        KEY_KPEQUAL,
        KEY_KPPLUSMINUS,
        KEY_KPCOMMA,
        KEY_KPLEFTPAREN,
        KEY_KPRIGHTPAREN,
    }
)


def is_button(key_code: int) -> bool:
    """BTN_* ranges in Linux input-event-codes.h, including combo devices."""
    return (0x100 <= key_code < 0x160 or 0x220 <= key_code < 0x224
            or 0x2C0 <= key_code <= KEY_MAX)


def is_backspace(key_code: int) -> bool:
    return key_code in BACKSPACE_KEYS


def is_delete(key_code: int) -> bool:
    return key_code == KEY_DELETE


def is_modifier(key_code: int) -> bool:
    return key_code in MODIFIER_KEYS


def is_shift(key_code: int) -> bool:
    return key_code in SHIFT_KEYS


def is_navigation(key_code: int) -> bool:
    return key_code in NAVIGATION_KEYS


def is_shortcut_modifier(key_code: int) -> bool:
    return key_code in SHORTCUT_MODIFIER_KEYS


def is_enter(key_code: int) -> bool:
    return key_code in {KEY_ENTER, KEY_KPENTER}


def is_tab(key_code: int) -> bool:
    return key_code == KEY_TAB


def is_function(key_code: int) -> bool:
    return key_code in FUNCTION_KEYS


def is_keypad(key_code: int) -> bool:
    return key_code in KEYPAD_KEYS


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
class Kind(StrEnum):
    PRINTABLE = "printable"
    BACKSPACE = "backspace"
    DELETE = "delete"
    ENTER = "enter"
    TAB = "tab"
    ESCAPE = "escape"
    MODIFIER = "modifier"
    NAVIGATION = "navigation"
    FUNCTION = "function"
    OTHER = "other"


# Stable aliases so existing queries / tests keep working.
KIND_PRINTABLE = Kind.PRINTABLE
KIND_BACKSPACE = Kind.BACKSPACE
KIND_DELETE = Kind.DELETE
KIND_ENTER = Kind.ENTER
KIND_TAB = Kind.TAB
KIND_ESCAPE = Kind.ESCAPE
KIND_MODIFIER = Kind.MODIFIER
KIND_NAVIGATION = Kind.NAVIGATION
KIND_FUNCTION = Kind.FUNCTION
KIND_OTHER = Kind.OTHER


def classify_key(key_code: int) -> Kind:
    """Classify an evdev key code into a stats-friendly category.

    Keypad digits are printable here; the listener rewrites them to
    navigation when NumLock is off (that state is per-device).
    """
    if is_backspace(key_code):
        return Kind.BACKSPACE
    if is_delete(key_code):
        return Kind.DELETE
    if is_enter(key_code):
        return Kind.ENTER
    if is_tab(key_code):
        return Kind.TAB
    if key_code == KEY_ESC:
        return Kind.ESCAPE
    if is_modifier(key_code):
        return Kind.MODIFIER
    if is_function(key_code):
        return Kind.FUNCTION
    if is_navigation(key_code):
        return Kind.NAVIGATION
    if key_code in KEY_CHARS:
        return Kind.PRINTABLE
    return Kind.OTHER


# ---------------------------------------------------------------------------
# US English + keypad character maps
# ---------------------------------------------------------------------------
_BASE: frozendict[int, str] = frozendict({
    KEY_1: "1",
    KEY_2: "2",
    KEY_3: "3",
    KEY_4: "4",
    KEY_5: "5",
    KEY_6: "6",
    KEY_7: "7",
    KEY_8: "8",
    KEY_9: "9",
    KEY_0: "0",
    KEY_MINUS: "-",
    KEY_EQUAL: "=",
    KEY_LEFTBRACE: "[",
    KEY_RIGHTBRACE: "]",
    KEY_SEMICOLON: ";",
    KEY_APOSTROPHE: "'",
    KEY_GRAVE: "`",
    KEY_BACKSLASH: "\\",
    KEY_COMMA: ",",
    KEY_DOT: ".",
    KEY_SLASH: "/",
    KEY_SPACE: " ",
    KEY_102ND: "\\",
})

_SHIFTED: frozendict[int, str] = frozendict({
    KEY_1: "!",
    KEY_2: "@",
    KEY_3: "#",
    KEY_4: "$",
    KEY_5: "%",
    KEY_6: "^",
    KEY_7: "&",
    KEY_8: "*",
    KEY_9: "(",
    KEY_0: ")",
    KEY_MINUS: "_",
    KEY_EQUAL: "+",
    KEY_LEFTBRACE: "{",
    KEY_RIGHTBRACE: "}",
    KEY_SEMICOLON: ":",
    KEY_APOSTROPHE: '"',
    KEY_GRAVE: "~",
    KEY_BACKSLASH: "|",
    KEY_COMMA: "<",
    KEY_DOT: ">",
    KEY_SLASH: "?",
    KEY_SPACE: " ",
    KEY_102ND: "|",
})

_LETTERS: frozendict[int, str] = frozendict({
    KEY_Q: "q",
    KEY_W: "w",
    KEY_E: "e",
    KEY_R: "r",
    KEY_T: "t",
    KEY_Y: "y",
    KEY_U: "u",
    KEY_I: "i",
    KEY_O: "o",
    KEY_P: "p",
    KEY_A: "a",
    KEY_S: "s",
    KEY_D: "d",
    KEY_F: "f",
    KEY_G: "g",
    KEY_H: "h",
    KEY_J: "j",
    KEY_K: "k",
    KEY_L: "l",
    KEY_Z: "z",
    KEY_X: "x",
    KEY_C: "c",
    KEY_V: "v",
    KEY_B: "b",
    KEY_N: "n",
    KEY_M: "m",
})

# Produced when NumLock is on. Operators are always produced.
_KP_NUM: frozendict[int, str] = frozendict({
    KEY_KP0: "0",
    KEY_KP1: "1",
    KEY_KP2: "2",
    KEY_KP3: "3",
    KEY_KP4: "4",
    KEY_KP5: "5",
    KEY_KP6: "6",
    KEY_KP7: "7",
    KEY_KP8: "8",
    KEY_KP9: "9",
    KEY_KPDOT: ".",
    KEY_KPPLUS: "+",
    KEY_KPMINUS: "-",
    KEY_KPASTERISK: "*",
    KEY_KPSLASH: "/",
    KEY_KPEQUAL: "=",
    KEY_KPCOMMA: ",",
    KEY_KPLEFTPAREN: "(",
    KEY_KPRIGHTPAREN: ")",
})

_KP_ALWAYS: frozenset[int] = frozenset(
    {
        KEY_KPPLUS,
        KEY_KPMINUS,
        KEY_KPASTERISK,
        KEY_KPSLASH,
        KEY_KPEQUAL,
        KEY_KPCOMMA,
        KEY_KPLEFTPAREN,
        KEY_KPRIGHTPAREN,
    }
)

KEY_CHARS: frozendict[int, str] = frozendict({**_BASE, **_LETTERS, **_KP_NUM})


def base_char(key_code: int) -> str | None:
    return KEY_CHARS.get(key_code)


def shifted_char(key_code: int) -> str | None:
    if key_code in _SHIFTED:
        return _SHIFTED[key_code]
    letter = _LETTERS.get(key_code)
    return letter.upper() if letter else None


def char_for(
    key_code: int,
    shift: bool,
    caps: bool,
    numlock: bool = True,
) -> str | None:
    """Return the character a physical key produces, or None.

    Letters honour Shift XOR Caps Lock. Digits/punctuation honour Shift
    only. Keypad digits honour NumLock; keypad operators always emit.
    """
    if key_code in _KP_NUM:
        if key_code in _KP_ALWAYS:
            return _KP_NUM[key_code]
        return _KP_NUM[key_code] if numlock else None
    if key_code not in KEY_CHARS:
        return None
    if key_code in _LETTERS:
        return _LETTERS[key_code].upper() if (shift != caps) else _LETTERS[key_code]
    return _SHIFTED[key_code] if shift else _BASE[key_code]


def _build_names() -> dict[int, str]:
    names: dict[int, str] = {}
    for name, value in globals().items():
        if name.startswith("KEY_") and isinstance(value, int):
            names.setdefault(value, name)
    return names


KEY_NAMES: frozendict[int, str] = frozendict(_build_names())


def key_name(keycode: int) -> str:
    """Return the canonical evdev name (KEY_A) or KEY_<code>."""
    return KEY_NAMES.get(keycode, f"KEY_{keycode}")
