#!/usr/bin/env python3

import argparse
lazy import datetime
lazy import errno
lazy import fcntl
lazy import html
lazy import http.client
lazy import itertools
import json
import math
import os
lazy import signal
lazy import socket
lazy import ssl
import sys
lazy import tempfile
import time
lazy import urllib.error
lazy import urllib.parse
lazy import urllib.request
lazy import zlib
lazy import zoneinfo
lazy from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

# Consolidated WMO weather code lookup (daytime defaults).
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

# Night-time Nerd Font overrides for sky-condition WMO codes.
NIGHT_WEATHER_ICONS: dict[int, str] = {
    0: "",
    1: "",
    2: "",
}

# MET Norway locationforecast 2.0 base symbol codes -> standard WMO codes.
MET_TO_WMO: dict[str, int] = {
    "clearsky": 0,
    "fair": 1,
    "partlycloudy": 2,
    "cloudy": 3,
    "fog": 45,
    "lightrain": 61,
    "rain": 63,
    "heavyrain": 65,
    "lightrainshowers": 80,
    "rainshowers": 81,
    "heavyrainshowers": 82,
    "lightsleet": 71,
    "sleet": 73,
    "heavysleet": 75,
    "lightsleetshowers": 85,
    "sleetshowers": 85,
    "heavysleetshowers": 86,
    "lightsnow": 71,
    "snow": 73,
    "heavysnow": 75,
    "lightsnowshowers": 85,
    "snowshowers": 85,
    "heavysnowshowers": 86,
}

IMPERIAL_COUNTRIES: frozenset[str] = frozenset({"US", "LR", "MM"})

# Known datacenter / cloud / VPN provider ISP keywords.
_DATACENTER_KEYWORDS: frozenset[str] = frozenset({
    "cloudflare", "amazon", "aws", "google cloud", "microsoft", "azure",
    "digitalocean", "linode", "akamai", "ovh", "hetzner", "vultr", "oracle",
    "fastly", "mullvad", "nordvpn", "protonvpn", "proton ag", "expressvpn",
    "surfshark", "cyberghost", "private internet", "ipvanish", "windscribe",
    "hide.me", "tunnelbear", "hotspot shield", "zenmate", "kaspersky",
    "warp", "1.1.1.1", "m247", "datacamp", "choopa", "leaseweb", "contabo",
    "packethub", "scaleway", "worldstream", "gcore", "zenlayer", "ivpn",
    "tailscale",
})

FETCH_DEADLINE = 15.0
PER_ADDR_CONNECT_TIMEOUT = 2.0
BOOT_RETRY_THRESHOLD = 0.5
BOOT_RETRY_DELAY = 2.5
FAILURE_BACKOFF_SECONDS = 45
MAX_RESPONSE_BYTES = 1024 * 1024

HTTP_HEADERS: dict[str, str] = {
    "User-Agent": "dusky-waybar-weather/3.0 (Arch Linux Waybar module)",
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
}

type JsonDict = dict[str, Any]
type IpLocationResult = tuple[float | None, float | None, str, str, str, str]
type WeatherTuple = tuple[int, int, int, int, int | None, bool]


class _DeadlineExceeded(Exception):
    """Raised by SIGALRM when the overall refresh ceiling is reached."""


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
            return cls(
                source=source,
                unit_pref=unit_pref,
                lat=normalize_zero(float(lat)),
                lon=normalize_zero(float(lon)),
            )

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


@dataclass(slots=True)
class FetchContext:
    deadline: float
    debug: bool
    opener: Any = None
    made_network_call: bool = False

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def log(self, message: str) -> None:
        if self.debug:
            print(f"[weather] {message}", file=sys.stderr, flush=True)


def get_state_file() -> Path:
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config and Path(xdg_config).is_absolute() else Path.home() / ".config"
    return base / "dusky" / "settings" / "waybar_weather"


def get_runtime_dir() -> Path:
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime and Path(xdg_runtime).is_absolute():
        return Path(xdg_runtime)
    return Path(tempfile.gettempdir()) / f"dusky-waybar-weather-{os.getuid()}"


def normalize_zero(value: float) -> float:
    return 0.0 if value == 0.0 else value


def is_finite_number(value: object) -> bool:
    try:
        return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def valid_coordinates(lat: object, lon: object) -> bool:
    return is_finite_number(lat) and is_finite_number(lon) and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def parse_coord_pair(raw_lat: object, raw_lon: object) -> tuple[float, float] | None:
    try:
        lat = float(raw_lat) if isinstance(raw_lat, str) else raw_lat
        lon = float(raw_lon) if isinstance(raw_lon, str) else raw_lon
    except (ValueError, TypeError):
        return None

    if not valid_coordinates(lat, lon):
        return None
    return normalize_zero(float(lat)), normalize_zero(float(lon))


def parse_latitude(value: str) -> float:
    try:
        latitude = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Latitude must be a number.") from exc

    if not math.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
        raise argparse.ArgumentTypeError("Latitude must be between -90 and 90.")
    return normalize_zero(latitude)


def parse_longitude(value: str) -> float:
    try:
        longitude = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Longitude must be a number.") from exc

    if not math.isfinite(longitude) or not -180.0 <= longitude <= 180.0:
        raise argparse.ArgumentTypeError("Longitude must be between -180 and 180.")
    return normalize_zero(longitude)


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

    # Reject escaped lone surrogates so UTF-8 output and Rust JSON consumers stay valid.
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


def effective_ttl(interval: int) -> int:
    return max(1, interval - min(60, interval // 15))


def is_state_fresh(state: StateRecord, interval: int) -> bool:
    return 0.0 <= (time.time() - state.saved_at) < effective_ttl(interval)


def format_age(saved_at: float) -> str:
    age_seconds = max(0, int(time.time() - saved_at))
    if age_seconds < 60:
        return "<1m"
    if age_seconds < 3600:
        return f"{age_seconds // 60}m"
    if age_seconds < 86400:
        hours, remainder = divmod(age_seconds, 3600)
        minutes = remainder // 60
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, remainder = divmod(age_seconds, 86400)
    hours = remainder // 3600
    return f"{days}d {hours}h" if hours else f"{days}d"


def make_offline_payload(payload: JsonDict, saved_at: float) -> JsonDict:
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

    age_str = html.escape(format_age(saved_at), quote=False)
    offline_note = f"⚠ Offline — cached {age_str} ago"
    if "⚠ Offline —" not in tooltip:
        tooltip = f"{tooltip.rstrip()}\n\n<span color='#ff6b6b'>{offline_note}</span>".lstrip()

    cached["tooltip"] = tooltip
    cached["class"] = class_list
    return cached


def make_unavailable_payload() -> JsonDict:
    return {
        "text": "󰖐 N/A",
        "alt": "Unavailable",
        "tooltip": "<span color='#ff6b6b'>⚠ Weather unavailable — offline or upstream error</span>",
        "class": ["weather", "offline"],
    }


def load_state(state_file: Path) -> StateRecord | None:
    try:
        raw_text = state_file.read_text(encoding="utf-8")
        mtime = state_file.stat().st_mtime
    except (OSError, UnicodeError):
        return None

    try:
        raw = json.loads(raw_text)
    except (ValueError, RecursionError):
        return None

    if not isinstance(raw, dict) or raw.get("version") != 2:
        return None

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
    has_coords = valid_coordinates(cached_lat, cached_lon)

    return StateRecord(
        payload=payload,
        saved_at=float(saved_at),
        request_key=request_key,
        effective_unit=effective_unit,
        country_code=normalize_country_code(raw.get("country_code")),
        city=normalize_city(raw.get("city")),
        lat=normalize_zero(float(cached_lat)) if has_coords else None,
        lon=normalize_zero(float(cached_lon)) if has_coords else None,
    )


def write_json_file(path: Path, data: object) -> bool:
    """Publish complete JSON atomically; keep the old file if writing fails."""
    temp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            prefix=f".{path.name}.",
            suffix=".tmp",
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


def write_state(state_file: Path, record: StateRecord) -> bool:
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
    return write_json_file(state_file, wrapped)


def boottime_now() -> float:
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def load_backoff(backoff_file: Path, request_key: RequestKey) -> float | None:
    try:
        raw = json.loads(backoff_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None

    if not isinstance(raw, dict):
        return None
    if RequestKey.from_json(raw.get("request_key")) != request_key:
        return None

    failed_at = raw.get("failed_at")
    if not is_finite_number(failed_at):
        return None
    return float(failed_at)


def write_backoff(backoff_file: Path, request_key: RequestKey) -> None:
    write_json_file(
        backoff_file,
        {
            "failed_at": boottime_now(),
            "request_key": request_key.to_json(),
        },
    )


def clear_backoff(backoff_file: Path) -> None:
    with suppress(OSError):
        backoff_file.unlink(missing_ok=True)


def _build_https_opener() -> urllib.request.OpenerDirector:
    ssl_context = ssl.create_default_context()

    class InterleavedHTTPSConnection(http.client.HTTPSConnection):
        """Interleave IPv6/IPv4 targets with a short per-address connect cap."""

        def connect(self) -> None:
            total_timeout = (
                float(self.timeout)
                if isinstance(self.timeout, int | float) and self.timeout > 0
                else 5.0
            )
            deadline = time.monotonic() + total_timeout

            infos = socket.getaddrinfo(self.host, self.port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            if not infos:
                raise OSError(f"No addresses resolved for {self.host}:{self.port}")

            v6 = [info for info in infos if info[0] == socket.AF_INET6]
            v4 = [info for info in infos if info[0] == socket.AF_INET]
            other = [info for info in infos if info[0] not in (socket.AF_INET6, socket.AF_INET)]

            ordered = [
                info
                for pair in itertools.zip_longest(v6, v4)
                for info in pair
                if info is not None
            ]
            ordered.extend(other)

            sock: socket.socket | None = None
            last_err: OSError | None = None

            for af, socktype, proto, _canonname, sa in ordered:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    last_err = TimeoutError(f"Connection deadline exceeded for {self.host}")
                    break

                candidate: socket.socket | None = None
                try:
                    candidate = socket.socket(af, socktype, proto)
                    candidate.settimeout(min(PER_ADDR_CONNECT_TIMEOUT, remaining))
                    if self.source_address:
                        candidate.bind(self.source_address)
                    candidate.connect(sa)
                    sock = candidate
                    break
                except OSError as exc:
                    last_err = exc
                    if candidate is not None:
                        with suppress(OSError):
                            candidate.close()

            if sock is None:
                raise last_err if last_err is not None else OSError(f"Failed to connect to {self.host}")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with suppress(OSError):
                    sock.close()
                raise TimeoutError(f"TLS deadline exceeded for {self.host}")

            sock.settimeout(remaining)
            self.sock = sock
            try:
                if self._tunnel_host:
                    server_hostname = self._tunnel_host
                    self._tunnel()
                else:
                    server_hostname = self.host
                self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)
            except BaseException:
                with suppress(OSError):
                    if self.sock is not None:
                        self.sock.close()
                self.sock = None
                raise

    class InterleavedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
            return self.do_open(InterleavedHTTPSConnection, req, context=self._context)

    return urllib.request.build_opener(InterleavedHTTPSHandler(context=ssl_context))


def _is_local_network_error(exc: Exception) -> bool:
    reason: object = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        return True
    if isinstance(reason, OSError) and reason.errno in {
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
        errno.ENETDOWN,
        errno.EADDRNOTAVAIL,
    }:
        return True
    return False


def _decompress_gzip_bounded(raw_body: bytes) -> bytes | None:
    try:
        decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
        body = decomp.decompress(raw_body, MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES or decomp.unconsumed_tail:
            return None
        body += decomp.flush()
        if len(body) > MAX_RESPONSE_BYTES or not decomp.eof:
            return None
        return body
    except zlib.error:
        return None


def fetch_json(
    url: str,
    ctx: FetchContext,
    params: dict[str, object] | None = None,
    timeout: float = 5.0,
) -> JsonDict | None:
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"

    if ctx.opener is None:
        ctx.opener = _build_https_opener()

    for attempt in range(2):
        remaining = ctx.remaining()
        if remaining <= 0.2:
            ctx.log(f"skipping {url}: fetch deadline exhausted")
            return None

        call_timeout = min(timeout, remaining)
        request = urllib.request.Request(url, headers=HTTP_HEADERS)
        started = time.monotonic()
        is_first_call = not ctx.made_network_call

        try:
            with ctx.opener.open(request, timeout=call_timeout) as response:
                ctx.made_network_call = True
                if response.status != 200:
                    ctx.log(f"{url} -> HTTP {response.status}")
                    return None

                raw_body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw_body) > MAX_RESPONSE_BYTES:
                    ctx.log(f"{url} -> response exceeded {MAX_RESPONSE_BYTES} bytes")
                    return None

                length_header = response.headers.get("Content-Length")
                if length_header is not None:
                    try:
                        expected_len = int(length_header)
                    except ValueError:
                        ctx.log(f"{url} -> invalid Content-Length: {length_header!r}")
                        return None
                    if len(raw_body) != expected_len:
                        ctx.log(f"{url} -> truncated body ({len(raw_body)} != {expected_len})")
                        return None

                encoding = response.headers.get("Content-Encoding", "").strip().lower()
                if encoding == "gzip":
                    body = _decompress_gzip_bounded(raw_body)
                    if body is None:
                        ctx.log(f"{url} -> gzip decompression failed or exceeded cap")
                        return None
                elif encoding in {"", "identity"}:
                    body = raw_body
                else:
                    ctx.log(f"{url} -> unsupported Content-Encoding: {encoding!r}")
                    return None

                data = json.loads(body)
                if not isinstance(data, dict):
                    ctx.log(f"{url} -> JSON root is not an object")
                    return None
                ctx.log(f"{url} -> OK ({time.monotonic() - started:.2f}s)")
                return data
        except urllib.error.HTTPError as exc:
            ctx.made_network_call = True
            ctx.log(f"{url} -> HTTP {exc.code} ({time.monotonic() - started:.2f}s)")
            exc.close()
            return None
        except _DeadlineExceeded:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - started
            ctx.made_network_call = True
            ctx.log(f"{url} -> {type(exc).__name__}: {exc} ({elapsed:.2f}s)")
            if (
                attempt == 0
                and is_first_call
                and elapsed < BOOT_RETRY_THRESHOLD
                and _is_local_network_error(exc)
                and ctx.remaining() > (BOOT_RETRY_DELAY + 1.5)
            ):
                ctx.log(f"local network not ready; sleeping {BOOT_RETRY_DELAY:.1f}s before retry")
                time.sleep(BOOT_RETRY_DELAY)
                continue
            return None

    return None


def iana_to_utc_offset_str(tz_name: object) -> str:
    if not isinstance(tz_name, str) or not tz_name.strip():
        return ""
    try:
        tz = zoneinfo.ZoneInfo(tz_name.strip())
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return ""
    offset = datetime.datetime.now(tz).utcoffset()
    if offset is None:
        return ""
    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{sign}{hours:02d}:{minutes:02d}"


def extract_ipwho_location(data: JsonDict | None) -> IpLocationResult:
    if not data or data.get("success") is not True:
        return None, None, "", "", "", ""

    coords = parse_coord_pair(data.get("latitude"), data.get("longitude"))
    if coords is None:
        return None, None, "", "", "", ""

    tz = data.get("timezone")
    utc_offset = tz.get("utc", "") if isinstance(tz, dict) else ""

    conn = data.get("connection")
    isp = conn.get("isp", "") if isinstance(conn, dict) else ""

    return (
        coords[0],
        coords[1],
        normalize_country_code(data.get("country_code")),
        normalize_city(data.get("city")),
        str(utc_offset),
        str(isp),
    )


def extract_geojs_location(data: JsonDict | None) -> IpLocationResult:
    if not data:
        return None, None, "", "", "", ""

    coords = parse_coord_pair(data.get("latitude"), data.get("longitude"))
    if coords is None:
        return None, None, "", "", "", ""

    utc_offset = iana_to_utc_offset_str(data.get("timezone"))
    isp = data.get("organization_name") or data.get("organization") or ""

    return (
        coords[0],
        coords[1],
        normalize_country_code(data.get("country_code")),
        normalize_city(data.get("city")),
        utc_offset,
        str(isp),
    )


def extract_ipapi_location(data: JsonDict | None) -> IpLocationResult:
    if not data or data.get("error"):
        return None, None, "", "", "", ""

    coords = parse_coord_pair(data.get("latitude"), data.get("longitude"))
    if coords is None:
        return None, None, "", "", "", ""

    return (
        coords[0],
        coords[1],
        normalize_country_code(data.get("country_code")),
        normalize_city(data.get("city")),
        str(data.get("utc_offset", "")),
        str(data.get("org", "")),
    )


def get_ip_location(ctx: FetchContext) -> IpLocationResult:
    services = (
        ("https://ipwho.is/", extract_ipwho_location),
        ("https://get.geojs.io/v1/ip/geo.json", extract_geojs_location),
        ("https://ipapi.co/json/", extract_ipapi_location),
    )

    for url, extractor in services:
        location = extractor(fetch_json(url, ctx, timeout=3.0))
        if location[0] is not None and location[1] is not None:
            return location

    return None, None, "", "", "", ""


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_km = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return earth_radius_km * 2 * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def get_system_utc_offset_str() -> str:
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


def is_datacenter_isp(isp: str) -> bool:
    if not isp:
        return False
    isp_lower = isp.lower()
    return any(keyword in isp_lower for keyword in _DATACENTER_KEYWORDS)


def is_vpn_likely(
    ip_lat: float,
    ip_lon: float,
    ip_utc_offset: str,
    ip_isp: str,
    matching_state: StateRecord | None,
    ctx: FetchContext,
) -> bool:
    """Detect probable VPN/proxy exit nodes relative to matching IP-mode cache."""
    system_offset = parse_utc_offset_seconds(get_system_utc_offset_str())
    ip_offset = parse_utc_offset_seconds(ip_utc_offset)

    if system_offset is not None and ip_offset is not None:
        if abs(system_offset - ip_offset) > 3600:
            ctx.log(f"VPN likely: timezone mismatch (system={system_offset}s vs ip={ip_offset}s)")
            return True

    if (
        is_datacenter_isp(ip_isp)
        and matching_state is not None
        and matching_state.lat is not None
        and matching_state.lon is not None
    ):
        distance = haversine_km(matching_state.lat, matching_state.lon, ip_lat, ip_lon)
        if distance > 100.0:
            ctx.log(f"VPN likely: datacenter ISP {ip_isp!r} moved {distance:.1f}km from cache")
            return True

    return False


def reverse_geocode(lat: float, lon: float, ctx: FetchContext) -> tuple[str, str]:
    data = fetch_json(
        "https://api.bigdatacloud.net/data/reverse-geocode-client",
        ctx,
        params={
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "localityLanguage": "en",
        },
        timeout=3.5,
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

    if country_code:
        return "imperial" if country_code in IMPERIAL_COUNTRIES else "metric"

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


def parse_probability(value: object) -> int | None:
    try:
        if isinstance(value, str):
            value = float(value)
        probability = as_float(value)
        if 0.0 <= probability <= 100.0:
            return round_half_away_from_zero(probability)
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def parse_open_meteo_data(weather_data: JsonDict) -> WeatherTuple:
    current = weather_data.get("current")
    daily = weather_data.get("daily")

    if not isinstance(current, dict) or not isinstance(daily, dict):
        raise TypeError("Missing current or daily Open-Meteo data.")

    temp = round_half_away_from_zero(as_float(current.get("temperature_2m")))
    weather_code = as_int(current.get("weather_code"))

    raw_is_day = current.get("is_day")
    try:
        is_day = as_int(raw_is_day) != 0 if raw_is_day is not None else True
    except TypeError:
        is_day = True

    daily_temp_max = daily.get("temperature_2m_max")
    daily_temp_min = daily.get("temperature_2m_min")
    daily_precip = daily.get("precipitation_probability_max")

    if isinstance(daily_temp_max, list) and daily_temp_max and is_finite_number(daily_temp_max[0]):
        temp_max = round_half_away_from_zero(float(daily_temp_max[0]))
    else:
        temp_max = temp

    if isinstance(daily_temp_min, list) and daily_temp_min and is_finite_number(daily_temp_min[0]):
        temp_min = round_half_away_from_zero(float(daily_temp_min[0]))
    else:
        temp_min = temp

    precip_prob = (
        parse_probability(daily_precip[0])
        if isinstance(daily_precip, list) and daily_precip
        else None
    )

    return temp, weather_code, temp_max, temp_min, precip_prob, is_day


def _extract_met_symbol(data_obj: JsonDict) -> str:
    for period in ("next_1_hours", "next_6_hours", "next_12_hours"):
        block = data_obj.get(period)
        if isinstance(block, dict):
            summary = block.get("summary")
            if isinstance(summary, dict):
                code = summary.get("symbol_code")
                if isinstance(code, str) and code:
                    return code
    return ""


def _parse_iso_utc(value: object) -> datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_met_no_data(met_data: JsonDict, unit: str) -> WeatherTuple:
    properties = met_data.get("properties")
    if not isinstance(properties, dict):
        raise TypeError("Missing MET Norway properties.")

    timeseries = properties.get("timeseries")
    if not isinstance(timeseries, list) or not timeseries:
        raise TypeError("Missing MET Norway timeseries.")

    first = timeseries[0]
    if not isinstance(first, dict):
        raise TypeError("Invalid MET Norway first entry.")

    first_data = first.get("data")
    if not isinstance(first_data, dict):
        raise TypeError("Missing MET Norway data object.")

    instant = first_data.get("instant")
    instant_details = instant.get("details") if isinstance(instant, dict) else None
    if not isinstance(instant_details, dict):
        raise TypeError("Missing MET Norway instant details.")

    temp_c = as_float(instant_details.get("air_temperature"))

    symbol_code = _extract_met_symbol(first_data)
    is_day = not symbol_code.endswith("_night")
    base_symbol = symbol_code
    for suffix in ("_day", "_night", "_polartwilight"):
        if base_symbol.endswith(suffix):
            base_symbol = base_symbol[: -len(suffix)]
            break
    weather_code = MET_TO_WMO.get(base_symbol, 95 if "thunder" in base_symbol else -1)

    min_c = temp_c
    max_c = temp_c
    first_dt = _parse_iso_utc(first.get("time"))

    for idx, entry in enumerate(timeseries):
        if not isinstance(entry, dict):
            continue
        if first_dt is not None:
            entry_dt = _parse_iso_utc(entry.get("time"))
            if entry_dt is not None and (entry_dt - first_dt).total_seconds() > 86400:
                break
        elif idx >= 24:
            break

        entry_data = entry.get("data")
        if not isinstance(entry_data, dict):
            continue

        entry_instant = entry_data.get("instant")
        if isinstance(entry_instant, dict):
            details = entry_instant.get("details")
            if isinstance(details, dict):
                t_val = details.get("air_temperature")
                if is_finite_number(t_val):
                    t_float = float(t_val)
                    min_c = min(min_c, t_float)
                    max_c = max(max_c, t_float)

        next_6 = entry_data.get("next_6_hours")
        if isinstance(next_6, dict):
            details6 = next_6.get("details")
            if isinstance(details6, dict):
                t_max = details6.get("air_temperature_max")
                if is_finite_number(t_max):
                    max_c = max(max_c, float(t_max))
                t_min = details6.get("air_temperature_min")
                if is_finite_number(t_min):
                    min_c = min(min_c, float(t_min))

    if unit == "imperial":
        temp = round_half_away_from_zero(temp_c * 9.0 / 5.0 + 32.0)
        temp_max = round_half_away_from_zero(max_c * 9.0 / 5.0 + 32.0)
        temp_min = round_half_away_from_zero(min_c * 9.0 / 5.0 + 32.0)
    else:
        temp = round_half_away_from_zero(temp_c)
        temp_max = round_half_away_from_zero(max_c)
        temp_min = round_half_away_from_zero(min_c)

    return temp, weather_code, temp_max, temp_min, None, is_day


def build_weather_payload(
    temp: int,
    weather_code: int,
    temp_max: int,
    temp_min: int,
    precip_prob: int | None,
    is_day: bool,
    unit: str,
    city: str,
) -> JsonDict:
    default_icon, weather_desc = WEATHER_CODES.get(weather_code, ("", "Unknown"))
    icon = NIGHT_WEATHER_ICONS[weather_code] if (not is_day and weather_code in NIGHT_WEATHER_ICONS) else default_icon
    temp_symbol = "°F" if unit == "imperial" else "°C"

    safe_desc = html.escape(weather_desc, quote=False)
    precip_text = str(precip_prob) if precip_prob is not None else "?"
    tooltip = (
        f"<span size='xx-large'>{temp}{temp_symbol}</span>\n"
        f"<big>{icon} {safe_desc}</big>\n"
        f" {temp_max}{temp_symbol}   {temp_min}{temp_symbol}   {precip_text}%"
    )

    return {
        "text": f"{icon}   {temp}{temp_symbol}",
        "alt": city or "Weather",
        "tooltip": tooltip,
        "class": "weather",
    }


def fetch_weather(
    args: argparse.Namespace,
    request_key: RequestKey,
    matching_state: StateRecord | None,
    ctx: FetchContext,
) -> StateRecord | None:
    lat: float
    lon: float
    country_code = ""
    city = ""

    if args.lat is None:
        ip_lat, ip_lon, country_code, city, ip_utc_offset, ip_isp = get_ip_location(ctx)
        if ip_lat is None or ip_lon is None:
            if not matching_state or matching_state.lat is None or matching_state.lon is None:
                ctx.log("GeoIP failed and no cached coordinates are available")
                return None
            ctx.log("GeoIP failed; reusing cached coordinates")
            lat, lon = matching_state.lat, matching_state.lon
            country_code, city = matching_state.country_code, matching_state.city
        elif is_vpn_likely(ip_lat, ip_lon, ip_utc_offset, ip_isp, matching_state, ctx):
            if matching_state and matching_state.lat is not None and matching_state.lon is not None:
                lat, lon = matching_state.lat, matching_state.lon
                country_code = matching_state.country_code or country_code
                city = matching_state.city or city
            else:
                lat, lon = ip_lat, ip_lon
        else:
            lat, lon = ip_lat, ip_lon
    else:
        lat, lon = args.lat, args.lon
        if not args.celsius and not args.fahrenheit:
            if matching_state and matching_state.country_code:
                country_code = matching_state.country_code
                city = matching_state.city
                ctx.log(f"reusing cached reverse-geocode metadata ({country_code}, {city!r})")
            else:
                country_code, city = reverse_geocode(lat, lon, ctx)
                if not country_code and matching_state:
                    country_code = matching_state.country_code
                    city = city or matching_state.city
        elif matching_state:
            city = matching_state.city

    unit = resolve_unit(args, country_code, matching_state)
    temp_unit = "fahrenheit" if unit == "imperial" else "celsius"
    lat_q = f"{lat:.4f}"
    lon_q = f"{lon:.4f}"

    result: WeatherTuple | None = None

    weather_data = fetch_json(
        "https://api.open-meteo.com/v1/forecast",
        ctx,
        params={
            "latitude": lat_q,
            "longitude": lon_q,
            "current": "temperature_2m,weather_code,is_day",
            "temperature_unit": temp_unit,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 1,
        },
        timeout=6.0,
    )
    if weather_data and not weather_data.get("error"):
        try:
            result = parse_open_meteo_data(weather_data)
        except (TypeError, ValueError, OverflowError, IndexError, AttributeError) as exc:
            ctx.log(f"Open-Meteo parse error: {exc}")

    if result is None:
        met_data = fetch_json(
            "https://api.met.no/weatherapi/locationforecast/2.0/compact",
            ctx,
            params={"lat": lat_q, "lon": lon_q},
            timeout=6.0,
        )
        if met_data:
            try:
                result = parse_met_no_data(met_data, unit)
            except (TypeError, ValueError, OverflowError, IndexError, AttributeError) as exc:
                ctx.log(f"MET Norway parse error: {exc}")

    if result is None:
        return None

    temp, weather_code, temp_max, temp_min, precip_prob, is_day = result
    payload = build_weather_payload(
        temp=temp,
        weather_code=weather_code,
        temp_max=temp_max,
        temp_min=temp_min,
        precip_prob=precip_prob,
        is_day=is_day,
        unit=unit,
        city=city,
    )

    return StateRecord(
        payload=payload,
        saved_at=time.time(),
        request_key=request_key,
        effective_unit=unit,
        country_code=country_code,
        city=city,
        lat=lat,
        lon=lon,
    )


def emit_offline(matching_state: StateRecord | None) -> None:
    if matching_state is not None:
        emit_payload(make_offline_payload(matching_state.payload, matching_state.saved_at))
    else:
        emit_payload(make_unavailable_payload())


def main() -> None:
    parser = argparse.ArgumentParser(description="Waybar weather module")
    parser.add_argument("--lat", type=parse_latitude, help="Latitude override")
    parser.add_argument("--lon", type=parse_longitude, help="Longitude override")
    parser.add_argument(
        "-i",
        "--interval",
        type=parse_interval,
        default=3600,
        help="Cache freshness interval in seconds (default: 3600)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass cache freshness and failure backoff",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log diagnostic details to stderr",
    )

    unit_group = parser.add_mutually_exclusive_group()
    unit_group.add_argument("-c", "--celsius", action="store_true", help="Force Celsius")
    unit_group.add_argument("-f", "--fahrenheit", action="store_true", help="Force Fahrenheit")

    args = parser.parse_args()

    if (args.lat is None) != (args.lon is None):
        parser.error("Arguments --lat and --lon must be provided together.")

    request_key = RequestKey.from_args(args)
    state_file = get_state_file()

    # Fast cache-hit path: zero lock, TLS, or network imports.
    if not args.force:
        state = load_state(state_file)
        matching_state = state if state and state.request_key == request_key else None
        if matching_state and is_state_fresh(matching_state, args.interval):
            emit_payload(matching_state.payload)
            return

    ctx = FetchContext(deadline=time.monotonic() + FETCH_DEADLINE, debug=args.debug)
    runtime_dir = get_runtime_dir()
    lock_file = runtime_dir / "dusky-waybar-weather.lock"
    backoff_file = runtime_dir / "dusky-waybar-weather.backoff"

    def _on_alarm(_signum: int, _frame: Any) -> None:
        raise _DeadlineExceeded

    previous_handler = signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, FETCH_DEADLINE)

    matching_state: StateRecord | None = None
    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        with lock_file.open("ab") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)

            state = load_state(state_file)
            matching_state = state if state and state.request_key == request_key else None

            if not args.force and matching_state and is_state_fresh(matching_state, args.interval):
                ctx.log("coalesced onto fresh state written by concurrent invocation")
                emit_payload(matching_state.payload)
                return

            if not args.force:
                failed_at = load_backoff(backoff_file, request_key)
                backoff_window = min(args.interval, FAILURE_BACKOFF_SECONDS)
                if failed_at is not None and 0.0 <= (boottime_now() - failed_at) < backoff_window:
                    ctx.log("inside failure backoff window; emitting offline state")
                    emit_offline(matching_state)
                    return

            try:
                record = fetch_weather(args, request_key, matching_state, ctx)
            except _DeadlineExceeded:
                ctx.log(f"hard refresh deadline ({FETCH_DEADLINE:.1f}s) exceeded during fetch")
                write_backoff(backoff_file, request_key)
                emit_offline(matching_state)
                return

            if record is not None:
                clear_backoff(backoff_file)
                if not write_state(state_file, record):
                    ctx.log(f"failed to persist state to {state_file}; emitting live payload")
                emit_payload(record.payload)
                return

            write_backoff(backoff_file, request_key)
            emit_offline(matching_state)
    except _DeadlineExceeded:
        ctx.log(f"hard refresh deadline ({FETCH_DEADLINE:.1f}s) exceeded while waiting for lock")
        state = load_state(state_file)
        matching_state = state if state and state.request_key == request_key else None
        if not args.force and matching_state and is_state_fresh(matching_state, args.interval):
            emit_payload(matching_state.payload)
        else:
            emit_offline(matching_state)
    except OSError as exc:
        ctx.log(f"runtime lock error: {exc}")
        if matching_state is None:
            state = load_state(state_file)
            matching_state = state if state and state.request_key == request_key else None
        emit_offline(matching_state)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        null_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null_fd, sys.stdout.fileno())
        os.close(null_fd)
        sys.exit(1)
