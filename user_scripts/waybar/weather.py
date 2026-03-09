#!/usr/bin/env python3

import argparse
import http.client
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Self
from urllib.error import URLError

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

IMPERIAL_COUNTRIES = {"US", "LR", "MM"}
STATE_FILE = Path.home() / ".config" / "dusky" / "settings" / "waybar_weather"

HTTP_HEADERS = {
    "User-Agent": "waybar-weather/2.0 (Arch Linux; Python 3.14)",
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

        if source not in {"manual", "ip"} or unit_pref not in {"auto", "celsius", "fahrenheit"}:
            return None

        if source == "manual":
            if not is_finite_number(lat) or not is_finite_number(lon):
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


def is_finite_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


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
    return value.strip().upper() if isinstance(value, str) else ""


def normalize_city(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


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

    return {
        "text": text,
        "alt": alt,
        "tooltip": tooltip,
        "class": css_class,
    }


def emit_payload(payload: JsonDict) -> None:
    print(json_dumps(payload), flush=True)


def fail_gracefully(message: str, tooltip: str = "") -> None:
    emit_payload({
        "text": "󰖐 Err",
        "alt": "Error",
        "tooltip": tooltip or message,
        "class": "error",
    })
    raise SystemExit(0)


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

    offline_note = "⚠ Offline — showing cached weather"
    if offline_note not in tooltip:
        tooltip = f"{tooltip.rstrip()}\n\n<span color='#ff6b6b'>{offline_note}</span>".lstrip()

    cached["tooltip"] = tooltip
    cached["class"] = class_list
    return cached


def emit_cached_or_fail(state: StateRecord | None, error_tooltip: str) -> None:
    if state is not None:
        emit_payload(make_offline_payload(state.payload))
        raise SystemExit(0)

    fail_gracefully("Network Offline", error_tooltip)


def is_state_fresh(state: StateRecord, ttl_seconds: int) -> bool:
    return (time.time() - state.saved_at) < ttl_seconds


def load_state() -> StateRecord | None:
    try:
        raw_text = STATE_FILE.read_text(encoding="utf-8")
        mtime = STATE_FILE.stat().st_mtime
    except OSError:
        return None

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError:
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
        if effective_unit not in {"metric", "imperial"}:
            effective_unit = None

        return StateRecord(
            payload=payload,
            saved_at=float(saved_at),
            request_key=request_key,
            effective_unit=effective_unit,
            country_code=normalize_country_code(raw.get("country_code")),
            city=normalize_city(raw.get("city")),
        )

    # Legacy plain payload support.
    payload = normalize_payload(raw)
    if payload is None:
        return None

    return StateRecord(payload=payload, saved_at=mtime)


def write_state(record: StateRecord) -> None:
    wrapped: JsonDict = {
        "version": 2,
        "saved_at": record.saved_at,
        "payload": record.payload,
        "request_key": record.request_key.to_json() if record.request_key else None,
        "effective_unit": record.effective_unit,
        "country_code": record.country_code,
        "city": record.city,
    }

    data = json_dumps(wrapped)
    temp_path: Path | None = None

    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=STATE_FILE.parent,
            delete=False,
            prefix=f".{STATE_FILE.name}.",
            suffix=".tmp",
        ) as temp_file:
            temp_file.write(data)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)

        temp_path.replace(STATE_FILE)

        with suppress(OSError):
            dir_fd = os.open(STATE_FILE.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

    except OSError:
        with suppress(OSError):
            if temp_path is not None:
                temp_path.unlink()


def fetch_json(url: str, params: dict[str, object] | None = None, timeout: float = 5.0) -> JsonDict | None:
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"

    request = urllib.request.Request(url, headers=HTTP_HEADERS)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            data = json.loads(response.read())
    except (URLError, TimeoutError, OSError, http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError):
        return None

    return data if isinstance(data, dict) else None


def extract_ipwho_location(data: JsonDict | None) -> tuple[float | None, float | None, str, str]:
    if not data or data.get("success") is not True:
        return None, None, "", ""

    lat = data.get("latitude")
    lon = data.get("longitude")
    if not is_finite_number(lat) or not is_finite_number(lon):
        return None, None, "", ""

    return float(lat), float(lon), normalize_country_code(data.get("country_code")), normalize_city(data.get("city"))


def extract_ipapi_location(data: JsonDict | None) -> tuple[float | None, float | None, str, str]:
    if not data or data.get("error"):
        return None, None, "", ""

    lat = data.get("latitude")
    lon = data.get("longitude")
    if not is_finite_number(lat) or not is_finite_number(lon):
        return None, None, "", ""

    return float(lat), float(lon), normalize_country_code(data.get("country_code")), normalize_city(data.get("city"))


def get_ip_location() -> tuple[float | None, float | None, str, str]:
    services = (
        ("https://ipwho.is/", extract_ipwho_location),
        ("https://ipapi.co/json/", extract_ipapi_location),
    )

    for url, extractor in services:
        location = extractor(fetch_json(url, timeout=5.0))
        if location[0] is not None and location[1] is not None:
            return location

    return None, None, "", ""


def reverse_geocode(lat: float, lon: float) -> tuple[str, str]:
    # Primary: Open-Meteo reverse geocoding.
    data = fetch_json(
        "https://geocoding-api.open-meteo.com/v1/reverse",
        params={
            "latitude": lat,
            "longitude": lon,
            "count": 1,
            "language": "en",
        },
        timeout=5.0,
    )

    if data:
        results = data.get("results")
        if isinstance(results, list) and results:
            first = results[0]
            if isinstance(first, dict):
                country_code = normalize_country_code(first.get("country_code"))
                city = normalize_city(first.get("name"))
                if country_code:
                    return country_code, city

    # Fallback: BigDataCloud reverse geocoder.
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
    if country_code in IMPERIAL_COUNTRIES:
        return "imperial"
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


def parse_weather_data(weather_data: JsonDict) -> tuple[int, int, int, int, int]:
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

    if not isinstance(daily_precip, list) or not daily_precip or daily_precip[0] is None:
        precip_prob = 0
    else:
        precip_prob = round_half_away_from_zero(as_float(daily_precip[0]))

    return temp, weather_code, temp_max, temp_min, precip_prob


def build_weather_payload(
    temp: int,
    weather_code: int,
    temp_max: int,
    temp_min: int,
    precip_prob: int,
    unit: str,
    city: str,
) -> JsonDict:
    icon, weather_desc = WEATHER_CODES.get(weather_code, ("", "Unknown"))
    temp_symbol = "°F" if unit == "imperial" else "°C"

    tooltip = (
        f"<span size='xx-large'>{temp}{temp_symbol}</span>\n"
        f"<big>{icon} {weather_desc}</big>\n"
        f" {temp_max}{temp_symbol}   {temp_min}{temp_symbol}   {precip_prob}%"
    )

    return {
        "text": f"{icon}   {temp}{temp_symbol}",
        "alt": city or "Weather",
        "tooltip": tooltip,
        "class": "weather",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Waybar weather module")
    parser.add_argument("--lat", type=parse_latitude, help="Latitude override")
    parser.add_argument("--lon", type=parse_longitude, help="Longitude override")
    parser.add_argument(
        "-i",
        "--interval",
        type=parse_interval,
        default=3600,
        help="Update interval in seconds",
    )

    unit_group = parser.add_mutually_exclusive_group()
    unit_group.add_argument("-c", "--celsius", action="store_true", help="Force Celsius")
    unit_group.add_argument("-f", "--fahrenheit", action="store_true", help="Force Fahrenheit")

    args = parser.parse_args()

    if (args.lat is None) != (args.lon is None):
        parser.error("Arguments --lat and --lon must be provided together.")

    request_key = RequestKey.from_args(args)
    state = load_state()
    matching_state = state if state and state.request_key == request_key else None

    # Fast-path cache hit only when the cached request matches the current request.
    if matching_state and is_state_fresh(matching_state, args.interval):
        emit_payload(matching_state.payload)
        return

    lat: float
    lon: float
    country_code = ""
    city = ""

    if args.lat is None:
        ip_lat, ip_lon, country_code, city = get_ip_location()
        if ip_lat is None or ip_lon is None:
            emit_cached_or_fail(state, "Failed to determine location and no cached weather is available.")
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

    if not weather_data or weather_data.get("error"):
        reason = weather_data.get("reason") if isinstance(weather_data, dict) else None
        error_tooltip = str(reason) if reason else "Failed to fetch weather and no cached weather is available."
        emit_cached_or_fail(state, error_tooltip)

    try:
        temp, weather_code, temp_max, temp_min, precip_prob = parse_weather_data(weather_data)
    except (TypeError, ValueError, IndexError, AttributeError):
        emit_cached_or_fail(state, "Malformed response from the weather service.")

    payload = build_weather_payload(
        temp=temp,
        weather_code=weather_code,
        temp_max=temp_max,
        temp_min=temp_min,
        precip_prob=precip_prob,
        unit=unit,
        city=city,
    )

    write_state(
        StateRecord(
            payload=payload,
            saved_at=time.time(),
            request_key=request_key,
            effective_unit=unit,
            country_code=country_code,
            city=city,
        )
    )

    emit_payload(payload)


if __name__ == "__main__":
    main()
