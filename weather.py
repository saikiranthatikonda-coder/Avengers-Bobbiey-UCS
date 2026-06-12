"""Open-Meteo client. Free, no key. Defaults to Hyderabad."""

import time

import httpx


WMO_LABELS = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Rain showers", 81: "Heavy showers", 82: "Violent showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Storm w/ hail", 99: "Severe storm",
}

WMO_GLYPH = {
    0: "☀", 1: "🌤", 2: "⛅", 3: "☁",
    45: "🌫", 48: "🌫",
    51: "🌦", 53: "🌦", 55: "🌧",
    61: "🌧", 63: "🌧", 65: "🌧",
    66: "🌧", 67: "🌧",
    71: "🌨", 73: "🌨", 75: "❄",
    77: "❄",
    80: "🌦", 81: "🌧", 82: "🌧",
    85: "🌨", 86: "❄",
    95: "⛈", 96: "⛈", 99: "⛈",
}


class WeatherService:
    """Open-Meteo client. Accepts ad-hoc lat/lon at call time so the dashboard
    can re-fetch when the user's geolocation is detected. Each coord pair has
    its own short-lived cache."""

    def __init__(self, lat: float = 17.385, lon: float = 78.4867,
                 city: str = "Hyderabad", hub=None) -> None:
        self.lat, self.lon, self.city, self.hub = lat, lon, city, hub
        self._cache: dict[str, tuple[dict, float]] = {}

    async def get(self, lat: float | None = None, lon: float | None = None) -> dict | None:
        use_lat = float(lat) if lat is not None else self.lat
        use_lon = float(lon) if lon is not None else self.lon
        key = f"{use_lat:.3f},{use_lon:.3f}"
        cached = self._cache.get(key)
        if cached and time.time() < cached[1]:
            return cached[0]

        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": use_lat,
                        "longitude": use_lon,
                        "current": "temperature_2m,relative_humidity_2m,"
                                   "weather_code,wind_speed_10m,apparent_temperature",
                        "timezone": "auto",
                    },
                )
                r.raise_for_status()
                payload = r.json()
                data = payload.get("current", {}) or {}
        except Exception as e:
            if self.hub:
                await self.hub.broadcast({
                    "type": "log", "level": "warn",
                    "msg": f"weather fetch failed ({key}): {e}",
                })
            # serve stale cache if we have one
            if cached:
                return cached[0]
            return {"error": str(e)}

        code = int(data.get("weather_code") or 0)
        result = {
            "city": self.city if (lat is None and lon is None) else "Your location",
            "lat": use_lat,
            "lon": use_lon,
            "temp_c": data.get("temperature_2m"),
            "feels_c": data.get("apparent_temperature"),
            "humidity": data.get("relative_humidity_2m"),
            "wind_kmh": data.get("wind_speed_10m"),
            "code": code,
            "label": WMO_LABELS.get(code, "—"),
            "glyph": WMO_GLYPH.get(code, "◐"),
            "fetched_at": time.time(),
        }
        # 5-minute cache per coord pair
        self._cache[key] = (result, time.time() + 300)
        return result
