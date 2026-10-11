import time
from typing import Optional, Dict, Any
from fastapi import APIRouter, Query
import httpx

router = APIRouter(prefix="/weather", tags=["weather"])

# In-memory cache: (lat_round, lng_round) -> (timestamp, data)
WEATHER_CACHE: Dict[str, tuple[float, Any]] = {}
CACHE_TTL_SECONDS = 600 # 10 minutes

@router.get("")
async def get_weather(
    lat: float = Query(11.2588),
    lng: float = Query(75.7804)
):
    cache_key = f"{round(lat, 2)},{round(lng, 2)}"
    now = time.time()

    if cache_key in WEATHER_CACHE:
        cached_time, cached_data = WEATHER_CACHE[cache_key]
        if now - cached_time < CACHE_TTL_SECONDS:
            return cached_data

    # Fetch from Open-Meteo
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lng}"
        f"&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
        f"&hourly=temperature_2m,weather_code"
        f"&forecast_days=1"
    )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                # Parse cleanly
                current = data.get("current", {})
                hourly = data.get("hourly", {})
                
                # Format 6-hour slice for the hourly row
                hourly_slice = []
                times = hourly.get("time", [])[:6]
                temps = hourly.get("temperature_2m", [])[:6]
                codes = hourly.get("weather_code", [])[:6]
                for i in range(len(times)):
                    hour_label = times[i].split("T")[-1] if "T" in times[i] else times[i]
                    hourly_slice.append({
                        "time": hour_label,
                        "temp": temps[i] if i < len(temps) else 28.0,
                        "code": codes[i] if i < len(codes) else 0,
                    })

                weather_payload = {
                    "location": "Kozhikode Coast" if abs(lat - 11.2588) < 0.1 else "Exploration Area",
                    "lat": lat,
                    "lng": lng,
                    "temperature": current.get("temperature_2m", 28.5),
                    "humidity": current.get("relative_humidity_2m", 75),
                    "weather_code": current.get("weather_code", 0),
                    "wind_speed": current.get("wind_speed_10m", 12.0),
                    "hourly": hourly_slice,
                }
                WEATHER_CACHE[cache_key] = (now, weather_payload)
                return weather_payload
    except Exception as e:
        pass

    # Fallback mock weather for offline hackathon mode
    fallback = {
        "location": "Kozhikode Coast",
        "lat": lat,
        "lng": lng,
        "temperature": 29.2,
        "humidity": 78,
        "weather_code": 1, # Mainly clear
        "wind_speed": 14.5,
        "hourly": [
            {"time": "12:00", "temp": 30.5, "code": 1},
            {"time": "13:00", "temp": 31.0, "code": 1},
            {"time": "14:00", "temp": 30.2, "code": 2},
            {"time": "15:00", "temp": 29.8, "code": 2},
            {"time": "16:00", "temp": 28.9, "code": 3},
            {"time": "17:00", "temp": 28.1, "code": 1},
        ],
    }
    return fallback
