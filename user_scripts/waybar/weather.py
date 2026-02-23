#!/usr/bin/env python3

import json
import urllib.request


#################################### GEOLOCATION ###################################


def get_location_data():
    try:
        with urllib.request.urlopen("http://ip-api.com/json/", timeout=5) as response:
            data = json.loads(response.read().decode())
            return {
                "lat": data.get("lat"),
                "lon": data.get("lon"),
                "countryCode": data.get("countryCode", ""),
                "city": data.get("city", ""),
            }
    except Exception:
        return None


IMPERIAL_COUNTRIES = {"US", "LR", "MM"}

location = get_location_data()
if location and location["lat"] is not None and location["lon"] is not None:
    unit = "imperial" if location["countryCode"] in IMPERIAL_COUNTRIES else "metric"
    city = location["city"]
    lat = location["lat"]
    lon = location["lon"]
else:
    unit = "metric"
    city = ""
    lat = 0
    lon = 0


########################################## MAIN ##################################

weather_icons = {
    0: "",  # Clear sky
    1: "",  # Mainly clear
    2: "",  # Partly cloudy
    3: "",  # Overcast
    45: "󰖑",  # Fog
    48: "󰖑",  # Depositing rime fog
    51: "",  # Light drizzle
    53: "",  # Moderate drizzle
    55: "",  # Dense drizzle
    56: "",  # Light freezing drizzle
    57: "",  # Dense freezing drizzle
    61: "",  # Slight rain
    63: "",  # Moderate rain
    65: "",  # Heavy rain
    66: "",  # Light freezing rain
    67: "",  # Heavy freezing rain
    71: "",  # Slight snow
    73: "",  # Moderate snow
    75: "",  # Heavy snow
    77: "",  # Snow grains
    80: "",  # Slight rain showers
    81: "",  # Moderate rain showers
    82: "",  # Violent rain showers
    85: "",  # Slight snow showers
    86: "",  # Heavy snow showers
    95: "",  # Thunderstorm
    96: "",  # Thunderstorm with slight hail
    99: "",  # Thunderstorm with heavy hail
}

weather_descriptions = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

temp_unit = "fahrenheit" if unit == "imperial" else "celsius"
url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&temperature_unit={temp_unit}&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=auto"

try:
    with urllib.request.urlopen(url, timeout=10) as response:
        weather_data = json.loads(response.read().decode())
except Exception:
    out_data = {
        "text": "Weather Error",
        "alt": "Error",
        "tooltip": "Failed to fetch weather",
    }
    print(json.dumps(out_data))
    exit(1)

current = weather_data.get("current_weather", {})
temp = round(current.get("temperature", 0))
weather_code = current.get("weathercode", -1)

daily = weather_data.get("daily", {})
daily_temp_max = daily.get("temperature_2m_max", [])
daily_temp_min = daily.get("temperature_2m_min", [])
daily_precip = daily.get("precipitation_probability_max", [])

temp_max = round(daily_temp_max[0]) if daily_temp_max else temp
temp_min = round(daily_temp_min[0]) if daily_temp_min else temp
precip_prob = daily_precip[0] if daily_precip else 0

weather_desc = weather_descriptions.get(weather_code, "Unknown")

icon = weather_icons.get(weather_code, "")

temp_symbol = "°F" if unit == "imperial" else "°C"

tooltip_text = str.format(
    "\t\t{}\t\t\n{}\n{}\n{}  |  {}%",
    f'<span size="xx-large">{temp}{temp_symbol}</span>',
    f"<big>{icon}</big>",
    f"<big>{weather_desc}</big>",
    f":{temp_max}{temp_symbol}  :{temp_min}{temp_symbol}",
    precip_prob,
)

out_data = {
    "text": f"{icon}   {temp}{temp_symbol}",
    "alt": city if city else "Weather",
    "tooltip": tooltip_text,
}
print(json.dumps(out_data))
