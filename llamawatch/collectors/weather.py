"""Weather collector — Open-Meteo API, cached for 30 minutes."""

WIDGET_ID = "weather"
WIDGET_NAME = "Weather"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 2, "minW": 3, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_ICON = "☁️"
WIDGET_DESCRIPTION = "Local weather forecast"
WIDGET_CONFIG_SCHEMA = [
    {"key": "location", "label": "Location", "type": "text", "placeholder": "e.g. London, UK",
     "description": "Type a city name — coordinates are found automatically"},
]
WIDGET_CONFIG_REQUIRED = True
WIDGET_MULTI_INSTANCE = True

import json
import time
import urllib.parse
import urllib.request

_cache: dict = {}  # keyed by (lat, lon) tuple
_geocode_cache: dict = {}  # keyed by location name
_CACHE_TTL = 1800  # 30 minutes
_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search?name={query}&count=1&language=en&format=json"

_API_BASE = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
    "wind_speed_10m,weather_code"
    "&daily=weather_code,temperature_2m_max,temperature_2m_min"
    "&timezone=Europe/London&forecast_days=4"
)

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _wmo_to_condition(code: int) -> tuple[str, str]:
    """Convert WMO weather code to (condition string, icon name)."""
    if code == 0:
        return "Clear sky", "sunny"
    if 1 <= code <= 2:
        return "Partly cloudy", "partly_cloudy"
    if code == 3:
        return "Cloudy", "cloudy"
    if code in (45, 48):
        return "Fog", "fog"
    if 51 <= code <= 55:
        return "Drizzle", "drizzle"
    if 61 <= code <= 65:
        return "Rain", "rain"
    if 71 <= code <= 75:
        return "Snow", "snow"
    if 80 <= code <= 82:
        return "Rain showers", "rain"
    if 95 <= code <= 99:
        return "Thunderstorm", "thunderstorm"
    return "Unknown", "unknown"


def _null_result() -> dict:
    return {
        "temp": None,
        "feels_like": None,
        "condition": None,
        "wind_speed": None,
        "humidity": None,
        "icon": None,
        "forecast": [],
    }


def _geocode(location: str) -> tuple:
    """Geocode a location name to (lat, lon, display_name) using Open-Meteo."""
    if location in _geocode_cache:
        return _geocode_cache[location]
    try:
        url = _GEOCODE_URL.format(query=urllib.parse.quote(location))
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        results = data.get("results", [])
        if results:
            r = results[0]
            coords = (r["latitude"], r["longitude"], r.get("name", location))
            _geocode_cache[location] = coords
            return coords
    except Exception:
        pass
    return (None, None, None)


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Collect weather data — registry-compatible entry point."""
    wc = widget_config or {}
    lat = wc.get("lat")
    lon = wc.get("lon")
    location = wc.get("location", "")

    # If we have a location name but no coords, geocode it
    if location and (not lat or not lon):
        lat, lon, _name = _geocode(location)

    if not lat or not lon:
        return {"error": "Location not configured", "configured": False}
    result = collect_weather(lat=lat, lon=lon)
    result["location_name"] = location
    return result


def collect_weather(lat: float = None, lon: float = None) -> dict:
    """Return current weather and 3-day forecast for the given coordinates."""
    if lat is None or lon is None:
        return {"error": "Location not configured", "configured": False}

    now = time.time()

    # Cache is keyed per (lat, lon) pair to support multi-instance
    cache_key = (lat, lon)
    entry = _cache.get(cache_key, {"data": None, "ts": 0})

    # Return cached data if still fresh
    if entry["data"] is not None and (now - entry["ts"]) < _CACHE_TTL:
        return entry["data"]

    api_url = _API_BASE.format(lat=lat, lon=lon)

    try:
        req = urllib.request.Request(api_url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = json.loads(resp.read().decode())

        current = raw.get("current", {})
        daily = raw.get("daily", {})

        condition, icon = _wmo_to_condition(current.get("weather_code", -1))

        # Build 3-day forecast (skip today = index 0, take indices 1-3)
        forecast = []
        dates = daily.get("time", [])
        codes = daily.get("weather_code", [])
        highs = daily.get("temperature_2m_max", [])
        lows = daily.get("temperature_2m_min", [])

        for i in range(1, min(4, len(dates))):
            # Parse YYYY-MM-DD to get weekday
            year, month, day = (int(x) for x in dates[i].split("-"))
            import datetime
            weekday = datetime.date(year, month, day).weekday()
            day_name = _DAY_NAMES[weekday]

            fc_condition, fc_icon = _wmo_to_condition(codes[i] if i < len(codes) else -1)
            forecast.append({
                "day": day_name,
                "high": round(highs[i]) if i < len(highs) else None,
                "low": round(lows[i]) if i < len(lows) else None,
                "condition": fc_condition,
                "icon": fc_icon,
            })

        result = {
            "temp": current.get("temperature_2m"),
            "feels_like": current.get("apparent_temperature"),
            "condition": condition,
            "wind_speed": current.get("wind_speed_10m"),
            "humidity": current.get("relative_humidity_2m"),
            "icon": icon,
            "forecast": forecast,
        }

        _cache[cache_key] = {"data": result, "ts": now}
        return result

    except Exception:
        # Return last cached value if available, else nulls
        if entry["data"] is not None:
            return entry["data"]
        return _null_result()
