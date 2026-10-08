#!/usr/bin/env python3

import argparse
lazy import datetime
lazy import fcntl
import json
import math
import os
import sys
import time
lazy import urllib.parse
lazy import urllib.request
lazy import urllib.error
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
lazy from tempfile import NamedTemporaryFile
from typing import Any, Self

# Consolidated WMO weather code lookup.
WEATHER_CODES: dict[int, tuple[str, str]] = {
    0: ("", "Clear sky"),
    1: ("", "Mainly clear"),
    2: ("", "Partly cloudy"),
    3: ("", "Overcast"),
    45: ("󰖑", "Fog"),
    48: ("󰖑", "Depositing rime fog"),
    51: ("", "Light drizzle"),
    53: ("", "Moderate drizzle"),
    55: ("", "Dense drizzle"),
    56: ("", "Light freezing drizzle"),
    57: ("", "Dense freezing drizzle"),
    61: ("", "Slight rain"),
    63: ("", "Moderate rain"),
    65: ("", "Heavy rain"),
    66: ("", "Light freezing rain"),
    67: ("", "Heavy freezing rain"),
    71: ("", "Slight snow"),
    73: ("", "Moderate snow"),
    75: ("", "Heavy snow"),
    77: ("", "Snow grains"),
    80: ("", "Slight rain showers"),
    81: ("", "Moderate rain showers"),
    82: ("", "Violent rain showers"),
    85: ("", "Slight snow showers"),
    86: ("", "Heavy snow showers"),
    95: ("", "Thunderstorm"),
    96: ("", "Thunderstorm with slight hail"),
    99: ("", "Thunderstorm with heavy hail"),
}

# Mapping World Weather Online (WWO) weather codes (used by wttr.in) to standard WMO codes.
WWO_TO_WMO: dict[int, int] = {
    113: 0,   # Clear/Sunny -> Clear sky
    116: 2,   # Partly Cloudy -> Partly cloudy
    119: 2,   # Cloudy -> Partly cloudy
    122: 3,   # Overcast -> Overcast
    143: 45,  # Mist -> Fog
    176: 80,  # Patchy rain nearby -> Slight rain showers
    179: 85,  # Patchy snow nearby -> Slight snow showers
    182: 85,  # Patchy sleet nearby -> Slight snow showers
    185: 56,  # Patchy freezing drizzle nearby -> Light freezing drizzle
    200: 95,  # Thundery outbreaks nearby -> Thunderstorm
    227: 71,  # Blowing snow -> Slight snow
    230: 75,  # Blizzard -> Heavy snow
    248: 45,  # Fog -> Fog
    260: 48,  # Freezing fog -> Depositing rime fog
    263: 51,  # Patchy light drizzle -> Light drizzle
    266: 51,  # Light drizzle -> Light drizzle
    281: 56,  # Freezing drizzle -> Light freezing drizzle
    284: 57,  # Heavy freezing drizzle -> Dense freezing drizzle
    293: 61,  # Patchy light rain -> Slight rain
    296: 61,  # Light rain -> Slight rain
    299: 63,  # Moderate rain at times -> Moderate rain
    302: 63,  # Moderate rain -> Moderate rain
    305: 65,  # Heavy rain at times -> Heavy rain
    308: 65,  # Heavy rain -> Heavy rain
    311: 66,  # Light freezing rain -> Light freezing rain
    314: 67,  # Moderate or heavy freezing rain -> Heavy freezing rain
    317: 71,  # Light sleet -> Slight snow
    320: 73,  # Moderate or heavy sleet -> Moderate snow
    323: 71,  # Patchy light snow -> Slight snow
    326: 71,  # Light snow -> Slight snow
    329: 73,  # Moderate snow -> Moderate snow
    332: 73,  # Moderate snow -> Moderate snow
    335: 75,  # Heavy snow -> Heavy snow
    338: 75,  # Heavy snow -> Heavy snow
    350: 77,  # Ice pellets -> Snow grains
    353: 80,  # Light rain shower -> Slight rain showers
    356: 81,  # Moderate or heavy rain shower -> Moderate rain showers
    359: 82,  # Torrential rain shower -> Violent rain showers
    362: 85,  # Light sleet showers -> Slight snow showers
    365: 86,  # Moderate or heavy sleet showers -> Heavy snow showers
    368: 85,  # Light snow showers -> Slight snow showers
    371: 86,  # Moderate or heavy snow showers -> Heavy snow showers
    374: 85,  # Light showers of ice pellets -> Slight snow showers
    377: 86,  # Moderate or heavy showers of ice pellets -> Heavy snow showers
    386: 95,  # Patchy light rain in area with thunder -> Thunderstorm
    389: 95,  # Moderate or heavy rain with thunder; WWO does not imply hail
    392: 95,  # Patchy light snow in area with thunder -> Thunderstorm
    395: 95,  # Moderate or heavy snow with thunder; WWO does not imply hail
}


IMPERIAL_COUNTRIES = {"US", "LR", "MM"}
STATE_FILE = Path.home() / ".config" / "dusky" / "settings" / "waybar_weather"
TIME_STATE_FILE = Path.home() / ".config" / "dusky" / "settings" / "waybar_weather_time"
FETCH_DEADLINE = 40
MAX_RESPONSE_BYTES = 1024 * 1024
WTTR_HOSTS = ("wttr.in", "wttr.is")
RETRY_INTERVAL = 60

HTTP_HEADERS = {
    "User-Agent": "dusky-waybar-weather/2.0",
    "Accept": "application/json",
}

type JsonDict = dict[str, Any]
type CssClass = str | list[str]


@dataclass(slots=True, frozen=True)
class RequestKey:
    source: str
    unit_pref: str
    lat: float | None = None
    lon: float | None = None

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Self:
        source = "manual" if args.lat is not None else "ip"
        unit_pref = "fahrenheit" if args.fahrenheit else "celsius" if args.celsius else "auto"
        return cls(source=source, unit_pref=unit_pref, lat=args.lat, lon=args.lon)

    @classmethod
    def from_json(cls, raw: object) -> Self | None:
        if not isinstance(raw, dict):
            return None

        source = raw.get("source")
        unit_pref = raw.get("unit_pref")
        lat = raw.get("lat")
        lon = raw.get("lon")

        if not isinstance(source, str) or not isinstance(unit_pref, str):
            return None
        if source not in {"manual", "ip"} or unit_pref not in {"auto", "celsius", "fahrenheit"}:
            return None

        if source == "manual":
            if not valid_coordinates(lat, lon):
                return None
            return cls(source=source, unit_pref=unit_pref, lat=float(lat), lon=float(lon))

        return cls(source=source, unit_pref=unit_pref)

    def to_json(self) -> JsonDict:
        data: JsonDict = {
            "source": self.source,
            "unit_pref": self.unit_pref,
        }
        if self.source == "manual":
            data["lat"] = self.lat
            data["lon"] = self.lon
        return data


@dataclass(slots=True)
class StateRecord:
    payload: JsonDict
    saved_at: float
    request_key: RequestKey | None = None
    effective_unit: str | None = None
    country_code: str = ""
    city: str = ""
    lat: float | None = None
    lon: float | None = None


def is_finite_number(value: object) -> bool:
    try:
        return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def valid_coordinates(lat: object, lon: object) -> bool:
    return is_finite_number(lat) and is_finite_number(lon) and -90 <= lat <= 90 and -180 <= lon <= 180


def parse_latitude(value: str) -> float:
    try:
        latitude = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Latitude must be a number.") from exc

    if not math.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
        raise argparse.ArgumentTypeError("Latitude must be between -90 and 90.")
    return latitude


def parse_longitude(value: str) -> float:
    try:
        longitude = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Longitude must be a number.") from exc

    if not math.isfinite(longitude) or not -180.0 <= longitude <= 180.0:
        raise argparse.ArgumentTypeError("Longitude must be between -180 and 180.")
    return longitude


def parse_interval(value: str) -> int:
    try:
        interval = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Interval must be a positive integer.") from exc

    if interval <= 0:
        raise argparse.ArgumentTypeError("Interval must be greater than 0.")
    return interval


def json_dumps(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def normalize_country_code(value: object) -> str:
    code = value.strip().upper() if isinstance(value, str) else ""
    return code if len(code) == 2 and code.isascii() and code.isalpha() else ""


def normalize_city(value: object) -> str:
    city = value.strip() if isinstance(value, str) else ""
    try:
        city.encode("utf-8")
    except UnicodeError:
        return ""
    return city


def normalize_payload(raw: object) -> JsonDict | None:
    if not isinstance(raw, dict):
        return None

    text = raw.get("text")
    tooltip = raw.get("tooltip")
    alt = raw.get("alt", "Weather")
    css_class = raw.get("class", "weather")

    if not isinstance(text, str) or not isinstance(tooltip, str):
        return None

    if not isinstance(alt, str):
        alt = "Weather"

    if isinstance(css_class, list):
        css_class = [item for item in css_class if isinstance(item, str)] or ["weather"]
    elif not isinstance(css_class, str):
        css_class = "weather"

    # Escaped lone surrogates are legal to Python's JSON decoder but cannot
    # be emitted as UTF-8 or read by the Rust consumers.
    try:
        for value in (text, tooltip, alt, *(css_class if isinstance(css_class, list) else [css_class])):
            value.encode("utf-8")
    except UnicodeError:
        return None

    return {
        "text": text,
        "alt": alt,
        "tooltip": tooltip,
        "class": css_class,
    }


def emit_payload(payload: JsonDict) -> None:
    print(json_dumps(payload), flush=True)


def make_offline_payload(payload: JsonDict) -> JsonDict:
    cached = dict(payload)

    css_class = cached.get("class", "weather")
    if isinstance(css_class, str):
        class_list = [css_class]
    elif isinstance(css_class, list):
        class_list = [item for item in css_class if isinstance(item, str)] or ["weather"]
    else:
        class_list = ["weather"]

    if "offline" not in class_list:
        class_list.append("offline")

    tooltip = cached.get("tooltip", "")
    if not isinstance(tooltip, str):
        tooltip = ""

    offline_note = "⚠ Cached weather — refresh pending or unavailable"
    if offline_note not in tooltip:
        tooltip = f"{tooltip.rstrip()}\n\n<span color='#ff6b6b'>{offline_note}</span>".lstrip()

    cached["tooltip"] = tooltip
    cached["class"] = class_list
    return cached


def load_time_state(state: StateRecord | None, request_key: RequestKey | None = None) -> tuple[float, float]:
    try:
        raw_text = TIME_STATE_FILE.read_text(encoding="utf-8")
        data = json.loads(raw_text)
        if not isinstance(data, dict):
            raise ValueError("Invalid weather timing state")
        stored_key = RequestKey.from_json(data.get("request_key"))
        if request_key is not None and stored_key != request_key:
            timestamp = state.saved_at if state and 0 <= state.saved_at <= time.time() else 0.0
            return timestamp, timestamp
        attempt, success = data.get("last_attempt", 0), data.get("last_success", 0)
        if not is_finite_number(attempt) or not is_finite_number(success):
            raise ValueError("Invalid weather timestamps")
        now = time.time()
        return float(attempt) if 0 <= attempt <= now else 0.0, float(success) if 0 <= success <= now else 0.0
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        if state is not None and 0 <= state.saved_at <= time.time():
            return state.saved_at, state.saved_at
        return 0.0, 0.0


def write_time_state(last_attempt: float, last_success: float, request_key: RequestKey | None = None) -> None:
    data = {
        "last_attempt": last_attempt,
        "last_success": last_success,
        "request_key": request_key.to_json() if request_key else None,
    }
    write_json_file(TIME_STATE_FILE, data)


def spawn_background_fetch(args: argparse.Namespace) -> bool:
    script_path = os.path.abspath(__file__)
    args_to_pass = ["--interval", str(args.interval)]
    if args.lat is not None:
        args_to_pass += ["--lat", str(args.lat), "--lon", str(args.lon)]
    if args.fahrenheit:
        args_to_pass.append("--fahrenheit")
    elif args.celsius:
        args_to_pass.append("--celsius")
    try:
        # This short-lived launcher deliberately detaches instead of waiting.
        # posix_spawn avoids retaining a Popen object for a running worker.
        os.posix_spawn(
            "/usr/bin/timeout",
            ["/usr/bin/timeout", "--signal=KILL", str(FETCH_DEADLINE),
             sys.executable, script_path, "--background-fetch", *args_to_pass],
            os.environ,
            file_actions=[(os.POSIX_SPAWN_OPEN, fd, os.devnull, os.O_RDWR, 0) for fd in (0, 1, 2)]
                         + [(os.POSIX_SPAWN_CLOSEFROM, 3)],
            setsid=True,
        )
        return True
    except OSError:
        return False


def is_state_fresh(state: StateRecord, ttl_seconds: int) -> bool:
    return 0 <= (time.time() - state.saved_at) < ttl_seconds


def load_state() -> StateRecord | None:
    try:
        raw_text = STATE_FILE.read_text(encoding="utf-8")
        mtime = STATE_FILE.stat().st_mtime
    except (OSError, UnicodeError):
        return None

    try:
        raw = json.loads(raw_text)
    except (ValueError, RecursionError):
        return None

    # New wrapped state format.
    if isinstance(raw, dict) and raw.get("version") == 2:
        payload = normalize_payload(raw.get("payload"))
        if payload is None:
            return None

        saved_at = raw.get("saved_at")
        if not is_finite_number(saved_at):
            saved_at = mtime

        request_key = RequestKey.from_json(raw.get("request_key"))

        effective_unit = raw.get("effective_unit")
        if not isinstance(effective_unit, str) or effective_unit not in {"metric", "imperial"}:
            effective_unit = None

        cached_lat = raw.get("lat")
        cached_lon = raw.get("lon")

        return StateRecord(
            payload=payload,
            saved_at=float(saved_at),
            request_key=request_key,
            effective_unit=effective_unit,
            country_code=normalize_country_code(raw.get("country_code")),
            city=normalize_city(raw.get("city")),
            lat=float(cached_lat) if valid_coordinates(cached_lat, cached_lon) else None,
            lon=float(cached_lon) if valid_coordinates(cached_lat, cached_lon) else None,
        )

    # Legacy plain payload support.
    payload = normalize_payload(raw)
    if payload is None:
        return None

    return StateRecord(payload=payload, saved_at=mtime)


def write_state(record: StateRecord) -> bool:
    wrapped: JsonDict = {
        "version": 2,
        "saved_at": record.saved_at,
        "payload": record.payload,
        "request_key": record.request_key.to_json() if record.request_key else None,
        "effective_unit": record.effective_unit,
        "country_code": record.country_code,
        "city": record.city,
        "lat": record.lat,
        "lon": record.lon,
    }

    return write_json_file(STATE_FILE, wrapped)


def write_json_file(path: Path, data: object) -> bool:
    """Publish complete JSON atomically; keep the old file if writing fails."""
    temp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False,
            prefix=f".{path.name}.", suffix=".tmp",
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(json_dumps(data))
        temp_path.replace(path)
        return True
    except (OSError, UnicodeError):
        return False
    finally:
        if temp_path is not None:
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)


def fetch_json(url: str, params: dict[str, object] | None = None, timeout: float = 5.0) -> JsonDict | None:
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"

    request = urllib.request.Request(url, headers=HTTP_HEADERS)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                return None
            # A bounded HTTPResponse.read() does not reject a short body itself.
            length = response.headers.get("Content-Length")
            if length is not None and len(body) != int(length):
                return None
            data = json.loads(body)
    except urllib.error.HTTPError as exc:
        exc.close()
        return None
    except Exception:
        return None

    return data if isinstance(data, dict) else None


# IP location result: (lat, lon, country_code, city, utc_offset_str, isp)
type IpLocationResult = tuple[float | None, float | None, str, str, str, str]


def extract_ipwho_location(data: JsonDict | None) -> IpLocationResult:
    if not data or data.get("success") is not True:
        return None, None, "", "", "", ""

    lat = data.get("latitude")
    lon = data.get("longitude")
    if not valid_coordinates(lat, lon):
        return None, None, "", "", "", ""

    tz = data.get("timezone", {})
    utc_offset = tz.get("utc", "") if isinstance(tz, dict) else ""

    conn = data.get("connection", {})
    isp = conn.get("isp", "") if isinstance(conn, dict) else ""

    return float(lat), float(lon), normalize_country_code(data.get("country_code")), normalize_city(data.get("city")), str(utc_offset), str(isp)


def extract_ipapi_location(data: JsonDict | None) -> IpLocationResult:
    if not data or data.get("error"):
        return None, None, "", "", "", ""

    lat = data.get("latitude")
    lon = data.get("longitude")
    if not valid_coordinates(lat, lon):
        return None, None, "", "", "", ""

    utc_offset = data.get("utc_offset", "")
    isp = data.get("org", "")

    return float(lat), float(lon), normalize_country_code(data.get("country_code")), normalize_city(data.get("city")), str(utc_offset), str(isp)


def get_ip_location() -> IpLocationResult:
    services = (
        ("https://ipwho.is/", extract_ipwho_location),
        ("https://ipapi.co/json/", extract_ipapi_location),
    )

    for url, extractor in services:
        location = extractor(fetch_json(url, timeout=5.0))
        if location[0] is not None and location[1] is not None:
            return location

    return None, None, "", "", "", ""


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def get_system_utc_offset_str() -> str:
    """Return system UTC offset as a string like '+05:30' or '-08:00'."""
    offset = datetime.datetime.now(datetime.timezone.utc).astimezone().utcoffset()
    if offset is None:
        return ""
    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{sign}{hours:02d}:{minutes:02d}"


def parse_utc_offset_seconds(offset_str: str) -> int | None:
    """Parse a UTC offset string like '+05:30' or '+0530' into seconds."""
    if not offset_str:
        return None
    s = offset_str.strip()
    if not s:
        return None
    sign = 1
    if s[0] in "+-":
        sign = -1 if s[0] == "-" else 1
        s = s[1:]
    if len(s) == 5 and s[2] == ":":
        s = s[:2] + s[3:]
    if len(s) != 4 or not s.isascii() or not s.isdigit():
        return None
    hours = int(s[:-2])
    minutes = int(s[-2:])
    if hours > 23 or minutes > 59:
        return None
    return sign * (hours * 3600 + minutes * 60)


# Known datacenter / cloud / VPN provider keywords.
# These ISPs host VPN exit nodes, CDN edges, or cloud instances — not residential users.
_DATACENTER_KEYWORDS: frozenset[str] = frozenset({
    "cloudflare", "amazon", "aws", "google cloud", "microsoft", "azure",
    "digitalocean", "linode", "akamai", "ovh", "hetzner", "vultr", "oracle",
    "fastly", "mullvad", "nordvpn", "protonvpn", "proton ag", "expressvpn",
    "surfshark", "cyberghost", "private internet", "ipvanish", "windscribe",
    "hide.me", "tunnelbear", "hotspot shield", "zenmate", "kaspersky",
    "warp", "1.1.1.1",
})


def is_datacenter_isp(isp: str) -> bool:
    """Check if the ISP name matches a known datacenter / VPN provider."""
    if not isp:
        return False
    isp_lower = isp.lower()
    return any(keyword in isp_lower for keyword in _DATACENTER_KEYWORDS)


def is_vpn_likely(
    ip_lat: float,
    ip_lon: float,
    ip_utc_offset: str,
    ip_isp: str,
    cached_state: StateRecord | None,
) -> bool:
    """Detect probable VPN/proxy using three layers:

    Layer 1: Timezone mismatch — IP-reported UTC offset vs system UTC offset.
             Catches cross-timezone VPNs (e.g. US exit node while in India).
    Layer 2: Datacenter ISP + distance — IP comes from a known cloud/VPN
             provider AND location jumped >100 km from cached.
             Catches same-timezone VPNs (e.g. Cloudflare WARP routing to Delhi).
    Layer 3: Normal ISP + distance — IP comes from a regular residential ISP
             but location changed. This is NOT flagged as VPN, allowing the
             script to self-correct if a previous cache was set during VPN use.

    Returns True if the IP location is likely from a VPN exit node.
    """
    # Layer 1: Timezone mismatch.
    system_offset = parse_utc_offset_seconds(get_system_utc_offset_str())
    ip_offset = parse_utc_offset_seconds(ip_utc_offset)

    if system_offset is not None and ip_offset is not None:
        if abs(system_offset - ip_offset) > 3600:
            return True

    # Layer 2: Datacenter ISP + significant location change.
    datacenter = is_datacenter_isp(ip_isp)

    if (
        datacenter
        and cached_state is not None
        and cached_state.lat is not None
        and cached_state.lon is not None
    ):
        distance = haversine_km(cached_state.lat, cached_state.lon, ip_lat, ip_lon)
        if distance > 100:
            return True

    # Layer 3 (implicit): Normal ISP with distance change → NOT VPN.
    # This allows self-correction when VPN is turned off.
    return False


def reverse_geocode(lat: float, lon: float) -> tuple[str, str]:
    # Primary: BigDataCloud reverse geocoder.
    data = fetch_json(
        "https://api.bigdatacloud.net/data/reverse-geocode-client",
        params={
            "latitude": lat,
            "longitude": lon,
            "localityLanguage": "en",
        },
        timeout=5.0,
    )

    if data:
        country_code = normalize_country_code(data.get("countryCode"))
        city = normalize_city(data.get("city")) or normalize_city(data.get("locality"))
        if country_code:
            return country_code, city

    return "", ""


def resolve_unit(
    args: argparse.Namespace,
    country_code: str,
    matching_state: StateRecord | None,
) -> str:
    if args.fahrenheit:
        return "imperial"
    if args.celsius:
        return "metric"
        
    # If geolocation succeeded, unconditionally establish the unit by region.
    if country_code:
        return "imperial" if country_code in IMPERIAL_COUNTRIES else "metric"
        
    # Fallback only when offline or if geolocation explicitly failed.
    if matching_state and matching_state.effective_unit in {"metric", "imperial"}:
        return matching_state.effective_unit
        
    return "metric"


def as_float(value: object) -> float:
    if not is_finite_number(value):
        raise TypeError("Expected a finite number.")
    return float(value)


def as_int(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("Booleans are not valid integers.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    raise TypeError("Expected an integer.")


def round_half_away_from_zero(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)


def parse_weather_data(weather_data: JsonDict) -> tuple[int, int, int, int, int | None]:
    current = weather_data.get("current")
    daily = weather_data.get("daily")

    if not isinstance(current, dict) or not isinstance(daily, dict):
        raise TypeError("Missing current or daily weather data.")

    temp = round_half_away_from_zero(as_float(current.get("temperature_2m")))
    weather_code = as_int(current.get("weather_code"))

    daily_temp_max = daily.get("temperature_2m_max")
    daily_temp_min = daily.get("temperature_2m_min")
    daily_precip = daily.get("precipitation_probability_max")

    if not isinstance(daily_temp_max, list) or not daily_temp_max:
        temp_max = temp
    else:
        temp_max = round_half_away_from_zero(as_float(daily_temp_max[0]))

    if not isinstance(daily_temp_min, list) or not daily_temp_min:
        temp_min = temp
    else:
        temp_min = round_half_away_from_zero(as_float(daily_temp_min[0]))

    precip_prob = parse_probability(daily_precip[0]) if isinstance(daily_precip, list) and daily_precip else None

    return temp, weather_code, temp_max, temp_min, precip_prob


def parse_probability(value: object) -> int | None:
    """Optional forecast fields must not invalidate a usable temperature."""
    try:
        if isinstance(value, str):
            value = float(value)
        probability = as_float(value)
        if 0 <= probability <= 100:
            return round_half_away_from_zero(probability)
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def wttr_number(value: object) -> float:
    # WWO uses numeric strings; reject booleans, NaN and infinity explicitly.
    return as_float(float(value) if isinstance(value, str) else value)


def parse_wttr_data(wttr_data: JsonDict, unit: str) -> tuple[int, int, int, int, int | None]:
    # The WWO upstream can wrap the same j1 schema in a "data" object.
    data = wttr_data.get("data", wttr_data)
    if not isinstance(data, dict):
        raise TypeError("Invalid wttr.in data wrapper.")
    current_cond = data.get("current_condition")
    weather_list = data.get("weather")
    if not isinstance(current_cond, list) or not current_cond or not isinstance(weather_list, list) or not weather_list:
        raise TypeError("Missing wttr.in condition or weather data.")
    current, today = current_cond[0], weather_list[0]
    if not isinstance(current, dict) or not isinstance(today, dict):
        raise TypeError("Invalid wttr.in condition or weather data.")

    temp_key = "temp_F" if unit == "imperial" else "temp_C"
    max_key = "maxtempF" if unit == "imperial" else "maxtempC"
    min_key = "mintempF" if unit == "imperial" else "mintempC"
    temp = round_half_away_from_zero(wttr_number(current.get(temp_key)))
    temp_max = round_half_away_from_zero(wttr_number(today.get(max_key)))
    temp_min = round_half_away_from_zero(wttr_number(today.get(min_key)))
    raw_code = current.get("weatherCode")
    wwo_code = as_int(wttr_number(raw_code)) if raw_code is not None else -1
    weather_code = WWO_TO_WMO.get(wwo_code, -1)

    # Rain and snow are separate WWO fields; take the daily maximum of either.
    # This is a fallback estimate, not a computed probability of their union.
    precip_prob = None
    hourly = today.get("hourly")
    if isinstance(hourly, list):
        for hour in hourly:
            if not isinstance(hour, dict):
                continue
            for key in ("chanceofrain", "chanceofsnow"):
                probability = parse_probability(hour.get(key))
                if probability is not None:
                    precip_prob = probability if precip_prob is None else max(precip_prob, probability)
    return temp, weather_code, temp_max, temp_min, precip_prob


def build_weather_payload(
    temp: int,
    weather_code: int,
    temp_max: int,
    temp_min: int,
    precip_prob: int | None,
    unit: str,
    city: str,
) -> JsonDict:
    icon, weather_desc = WEATHER_CODES.get(weather_code, ("", "Unknown"))
    temp_symbol = "°F" if unit == "imperial" else "°C"

    precip_text = str(precip_prob) if precip_prob is not None else "?"
    tooltip = (
        f"<span size='xx-large'>{temp}{temp_symbol}</span>\n"
        f"<big>{icon} {weather_desc}</big>\n"
        f" {temp_max}{temp_symbol}   {temp_min}{temp_symbol}   {precip_text}%"
    )

    return {
        "text": f"{icon}   {temp}{temp_symbol}",
        "alt": city or "Weather",
        "tooltip": tooltip,
        "class": "weather",
    }


def refresh_weather(args: argparse.Namespace, request_key: RequestKey, state: StateRecord | None) -> None:
    """Fetch under the caller's lock; failures preserve the last valid cache."""
    matching_state = state if state and state.request_key == request_key else None
    current_time = time.time()
    _, last_success = load_time_state(matching_state, request_key)
    if matching_state and is_state_fresh(matching_state, args.interval):
        return
    write_time_state(current_time, last_success, request_key)
    # Worker mode: do the actual network requests
    lat: float
    lon: float
    country_code = ""
    city = ""

    if args.lat is None:
        ip_lat, ip_lon, country_code, city, ip_utc_offset, ip_isp = get_ip_location()
        if ip_lat is None or ip_lon is None:
            # A location-service outage need not block the weather provider.
            if not matching_state or matching_state.lat is None or matching_state.lon is None:
                return
            lat, lon = matching_state.lat, matching_state.lon
            country_code, city = matching_state.country_code, matching_state.city
        # VPN/proxy detection: prefer cached location when VPN is likely.
        elif is_vpn_likely(ip_lat, ip_lon, ip_utc_offset, ip_isp, state):
            if state and state.lat is not None and state.lon is not None:
                lat, lon = state.lat, state.lon
                country_code = state.country_code or country_code
                city = state.city or city
            else:
                lat, lon = ip_lat, ip_lon
        else:
            lat, lon = ip_lat, ip_lon
    else:
        lat, lon = args.lat, args.lon
        if not args.celsius and not args.fahrenheit:
            country_code, city = reverse_geocode(lat, lon)
            if not country_code and matching_state:
                country_code = matching_state.country_code
                city = city or matching_state.city
        elif matching_state:
            city = matching_state.city

    unit = resolve_unit(args, country_code, matching_state)
    temp_unit = "fahrenheit" if unit == "imperial" else "celsius"

    weather_data = fetch_json(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code",
            "temperature_unit": temp_unit,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 1,
        },
        timeout=10.0,
    )

    result = None
    if weather_data and not weather_data.get("error"):
        try:
            result = parse_weather_data(weather_data)
        except (TypeError, ValueError, OverflowError, IndexError, AttributeError):
            pass

    if result is None:
        for host in WTTR_HOSTS:
            wttr_data = fetch_json(
                f"https://{host}/{lat},{lon}",
                params={"format": "j1"}, timeout=8.0,
            )
            if wttr_data and not wttr_data.get("error"):
                try:
                    result = parse_wttr_data(wttr_data, unit)
                except (TypeError, ValueError, OverflowError, IndexError, AttributeError):
                    continue
                break

    if result is None:
        return
    temp, weather_code, temp_max, temp_min, precip_prob = result

    payload = build_weather_payload(
        temp=temp,
        weather_code=weather_code,
        temp_max=temp_max,
        temp_min=temp_min,
        precip_prob=precip_prob,
        unit=unit,
        city=city,
    )

    completed_at = time.time()
    saved = write_state(
        StateRecord(
            payload=payload,
            saved_at=completed_at,
            request_key=request_key,
            effective_unit=unit,
            country_code=country_code,
            city=city,
            lat=lat,
            lon=lon,
        )
    )
    if saved:
        write_time_state(completed_at, completed_at, request_key)


def main() -> None:
    parser = argparse.ArgumentParser(description="Waybar weather module")
    parser.add_argument("--lat", type=parse_latitude, help="Latitude override")
    parser.add_argument("--lon", type=parse_longitude, help="Longitude override")
    parser.add_argument(
        "-i",
        "--interval",
        type=parse_interval,
        default=1800,
        help="Update interval in seconds",
    )
    parser.add_argument(
        "--background-fetch",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    unit_group = parser.add_mutually_exclusive_group()
    unit_group.add_argument("-c", "--celsius", action="store_true", help="Force Celsius")
    unit_group.add_argument("-f", "--fahrenheit", action="store_true", help="Force Fahrenheit")

    args = parser.parse_args()

    if (args.lat is None) != (args.lon is None):
        parser.error("Arguments --lat and --lon must be provided together.")

    request_key = RequestKey.from_args(args)
    if args.background_fetch:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with STATE_FILE.with_name('.waybar_weather.lock').open('ab') as worker_lock:
                fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                refresh_weather(args, request_key, load_state())
        except OSError:
            pass
        return

    state = load_state()
    matching_state = state if state and state.request_key == request_key else None

    current_time = time.time()
    last_attempt, last_success = load_time_state(matching_state, request_key)

    # Query mode: print cache and potentially spawn background worker
    is_fresh = matching_state and is_state_fresh(matching_state, args.interval)

    if is_fresh:
        emit_payload(matching_state.payload)
        return

    # Cache is stale or missing/unmatched
    # Retry failed/missing requests briefly; successful data keeps its full TTL.
    if current_time - last_attempt >= min(args.interval, RETRY_INTERVAL):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with STATE_FILE.with_name(".waybar_weather.launch.lock").open("ab") as launch_lock:
                fcntl.flock(launch_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                last_attempt, last_success = load_time_state(matching_state, request_key)
                if time.time() - last_attempt >= min(args.interval, RETRY_INTERVAL):
                    write_time_state(time.time(), last_success, request_key)
                    if not spawn_background_fetch(args):
                        write_time_state(0, last_success, request_key)
        except OSError:
            pass

    if matching_state:
        # Emit offline/stale payload
        emit_payload(make_offline_payload(matching_state.payload))
    else:
        emit_payload({
            "text": "󰖐 Loading...",
            "alt": "Loading",
            "tooltip": "Fetching weather in background...",
            "class": "weather",
        })


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # A stopped Waybar consumer can close the pipe before our output arrives.
        # Redirect it so interpreter shutdown does not try to flush it again.
        null_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null_fd, sys.stdout.fileno())
        os.close(null_fd)
        sys.exit(1)
