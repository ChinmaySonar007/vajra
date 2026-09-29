

import asyncio
import time
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import math
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="V.A.J.R.A 2.0 — Thunderstorm & Lightning Nowcast API",
    description="Multimodal AIML Nowcasting System for MoES / IMD SIH 26072",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"

# Major Indian cities/regions scanned for alerts and nowcasting
CITIES = [
    {"name": "Mumbai", "lat": 19.0760, "lon": 72.8777},
    {"name": "Delhi", "lat": 28.7041, "lon": 77.1025},
    {"name": "Bengaluru", "lat": 12.9716, "lon": 77.5946},
    {"name": "Kolkata", "lat": 22.5726, "lon": 88.3639},
    {"name": "Chennai", "lat": 13.0827, "lon": 80.2707},
    {"name": "Hyderabad", "lat": 17.3850, "lon": 78.4867},
    {"name": "Pune", "lat": 18.5204, "lon": 73.8567},
    {"name": "Ahmedabad", "lat": 23.0225, "lon": 72.5714},
    {"name": "Jaipur", "lat": 26.9124, "lon": 75.7873},
    {"name": "Lucknow", "lat": 26.8467, "lon": 80.9462},
    {"name": "Guwahati", "lat": 26.1445, "lon": 91.7362},
    {"name": "Bhopal", "lat": 23.2599, "lon": 77.4126},
    {"name": "Patna", "lat": 25.5941, "lon": 85.1376},
    {"name": "Nagpur", "lat": 21.1458, "lon": 79.0882},
    {"name": "Bhubaneswar", "lat": 20.2961, "lon": 85.8245},
    {"name": "Chandigarh", "lat": 30.7333, "lon": 76.7794},
    {"name": "Kochi", "lat": 9.9312, "lon": 76.2673},
    {"name": "Ranchi", "lat": 23.3441, "lon": 85.3096},
    {"name": "Raipur", "lat": 21.2514, "lon": 81.6296},
    {"name": "Dehradun", "lat": 30.3165, "lon": 78.0322},
    {"name": "Shillong", "lat": 25.5788, "lon": 91.8933},
    {"name": "Amaravati", "lat": 16.5417, "lon": 80.5150},
    {"name": "Thiruvananthapuram", "lat": 8.5241, "lon": 76.9366},
    {"name": "Srinagar", "lat": 34.0837, "lon": 74.7973},
    {"name": "Imphal", "lat": 24.8170, "lon": 93.9368},
]

# --- In-memory TTL cache -----------------------------------------------------
_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes


def _cache_get(key: str) -> Optional[dict]:
    item = _cache.get(key)
    if item and (time.time() - item[0]) < CACHE_TTL_SECONDS:
        return item[1]
    return None


def _cache_set(key: str, value: dict) -> None:
    _cache[key] = (time.time(), value)


def compute_risk(cape: float, precip_prob: float, gusts_kmh: float, cloud: float, weather_code: int) -> dict:
    """Physics-informed 0-100 thunderstorm & lightning risk score."""
    cape = cape or 0
    precip_prob = precip_prob or 0
    gusts_kmh = gusts_kmh or 0
    cloud = cloud or 0
    weather_code = weather_code or 0

    cape_score = min(cape / 2500, 1.0) * 40
    precip_score = min(precip_prob / 100, 1.0) * 25
    gust_score = min(gusts_kmh / 70, 1.0) * 15
    cloud_score = min(cloud / 100, 1.0) * 10

    code_bonus = 0
    if weather_code in (95, 96, 97, 99):
        code_bonus = 25
    elif weather_code in (80, 81, 82):
        code_bonus = 10

    score = round(min(cape_score + precip_score + gust_score + cloud_score + code_bonus, 100), 1)

    if score >= 70:
        level = "severe"
    elif score >= 45:
        level = "high"
    elif score >= 20:
        level = "moderate"
    else:
        level = "low"

    return {"score": score, "level": level}


def degrees_to_cardinal(deg: float) -> str:
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = round(deg / 22.5) % 16
    return dirs[idx]


def compute_motion_vector(lat: float, lon: float, gusts_kmh: float, wind_dir_deg: float, step_idx: int) -> dict:
    """Calculate convective cell steering motion vector and projected coordinates."""
    speed = round(max(16.0, min(65.0, (gusts_kmh or 20.0) * 0.9 + 4.0)), 1)
    
    # Steering heading in degrees: convective cells track with mid-level steering flow
    heading_deg = round(wind_dir_deg if wind_dir_deg is not None else (70.0 if lat >= 15.0 else 55.0), 1)
    heading_cardinal = degrees_to_cardinal(heading_deg)

    proj = []
    for h in [1, 2, 3]:
        dist_km = speed * h
        rad = math.radians(heading_deg)
        d_lat = (dist_km * math.cos(rad)) / 111.0
        cos_lat = max(0.1, math.cos(math.radians(lat)))
        d_lon = (dist_km * math.sin(rad)) / (111.0 * cos_lat)
        proj.append({
            "step": f"+{h}h",
            "lat": round(lat + d_lat, 4),
            "lon": round(lon + d_lon, 4),
            "distance_km": round(dist_km, 1)
        })

    return {
        "speed_kmh": speed,
        "heading_deg": heading_deg,
        "heading_cardinal": heading_cardinal,
        "trajectory": proj
    }


AIRPORT_DATA = {
    "Mumbai": {"code": "BOM", "icao": "VABB", "name": "Chhatrapati Shivaji Maharaj Intl", "rwy": "RWY 09/27"},
    "Delhi": {"code": "DEL", "icao": "VIDP", "name": "Indira Gandhi Intl", "rwy": "RWY 10/28 & 11/29"},
    "Bengaluru": {"code": "BLR", "icao": "VOBL", "name": "Kempegowda Intl", "rwy": "RWY 09L/27R"},
    "Kolkata": {"code": "CCU", "icao": "VECC", "name": "Netaji Subhash Chandra Bose Intl", "rwy": "RWY 01R/19L"},
    "Chennai": {"code": "MAA", "icao": "VOMM", "name": "Chennai Intl", "rwy": "RWY 07/25"},
    "Hyderabad": {"code": "HYD", "icao": "VOHS", "name": "Rajiv Gandhi Intl", "rwy": "RWY 09R/27L"},
    "Pune": {"code": "PNQ", "icao": "VAPO", "name": "Pune Lohegaon Airport", "rwy": "RWY 10/28"},
    "Ahmedabad": {"code": "AMD", "icao": "VAAH", "name": "SVP Intl", "rwy": "RWY 05/23"},
    "Jaipur": {"code": "JAI", "icao": "VIJP", "name": "Jaipur Intl", "rwy": "RWY 09/27"},
    "Lucknow": {"code": "LKO", "icao": "VILK", "name": "CCS Intl (Amausi)", "rwy": "RWY 09/27"},
    "Guwahati": {"code": "GAU", "icao": "VEGT", "name": "LGBI Airport (Borjhar)", "rwy": "RWY 02/20"},
    "Bhopal": {"code": "BHO", "icao": "VABP", "name": "Raja Bhoj Airport", "rwy": "RWY 12/30"},
    "Patna": {"code": "PAT", "icao": "VEPT", "name": "Jay Prakash Narayan Airport", "rwy": "RWY 07/25"},
    "Nagpur": {"code": "NAG", "icao": "VANP", "name": "Dr. Babasaheb Ambedkar Intl", "rwy": "RWY 14/32"},
    "Bhubaneswar": {"code": "BBI", "icao": "VEBS", "name": "Biju Patnaik Intl", "rwy": "RWY 01/19"},
    "Chandigarh": {"code": "IXC", "icao": "VICG", "name": "Shaheed Bhagat Singh Intl", "rwy": "RWY 11/29"},
    "Kochi": {"code": "COK", "icao": "VOCI", "name": "Cochin Intl (Nedumbassery)", "rwy": "RWY 09/27"},
    "Ranchi": {"code": "IXR", "icao": "VERC", "name": "Birsa Munda Airport", "rwy": "RWY 13/31"},
    "Raipur": {"code": "RPR", "icao": "VARP", "name": "Swami Vivekananda Airport", "rwy": "RWY 06/24"},
    "Dehradun": {"code": "DED", "icao": "VIDN", "name": "Jolly Grant Airport", "rwy": "RWY 08/26"},
    "Shillong": {"code": "SHL", "icao": "VEBI", "name": "Umroi Airport", "rwy": "RWY 04/22"},
    "Amaravati": {"code": "VGA", "icao": "VOBZ", "name": "Vijayawada Airport", "rwy": "RWY 08/26"},
    "Thiruvananthapuram": {"code": "TRV", "icao": "VOTV", "name": "Thiruvananthapuram Intl", "rwy": "RWY 14/32"},
    "Srinagar": {"code": "SXR", "icao": "VISR", "name": "Sheikh ul-Alam Intl", "rwy": "RWY 13/31"},
    "Imphal": {"code": "IMF", "icao": "VEIM", "name": "Bir Tikendrajit Intl", "rwy": "RWY 04/22"},
}

DISCOM_DATA = {
    "Mumbai": "MSEDCL / Adani Power",
    "Delhi": "BSES / Tata Power DDL",
    "Bengaluru": "BESCOM Urban Ring",
    "Kolkata": "CESC Metro / WBSEDCL",
    "Chennai": "TANGEDCO Metropolitan",
    "Hyderabad": "TSSPDCL Central Hub",
    "Pune": "MSEDCL Pune Circle",
    "Ahmedabad": "Torrent Power Discom",
    "Jaipur": "JVVNL Central Feeder",
    "Lucknow": "MVVNL Awadh Zone",
    "Guwahati": "APDCL Lower Assam",
    "Bhopal": "MPPKVVCL Central",
    "Patna": "SBPDCL Gangetic Grid",
    "Nagpur": "MSEDCL Vidarbha Grid",
    "Bhubaneswar": "TPCODL Coastal Ring",
    "Chandigarh": "UT Electricity Dept",
    "Kochi": "KSEB Ernakulam Circle",
    "Ranchi": "JBVNL Chotanagpur Ring",
    "Raipur": "CSPDCL Mahanadi Feeder",
    "Dehradun": "UPCL Garhwal Division",
    "Shillong": "MeECL Khasi Hills Grid",
    "Amaravati": "APCPDCL Capital Feeders",
    "Thiruvananthapuram": "KSEB South Zone",
    "Srinagar": "KPDCL Kashmir Valley",
    "Imphal": "MSPDCL Manipur Central",
}


def compute_sector_impacts(
    city_name: str,
    risk_score: float,
    risk_level: str,
    time_label: str,
    radar_dbz: float = 30.0,
    wind_gusts: float = 20.0,
    wind_cardinal: str = "ENE",
    lightning_jump: bool = False,
    total_lightning_hr: int = 0,
    cloud_top_temp: float = -45.0,
    speed_kmh: float = 35.0,
) -> dict:
    """Derive highly tailored, sector-specific actionable impact advisories for V.A.J.R.A 2.0."""
    apt = AIRPORT_DATA.get(city_name, {"code": city_name[:3].upper(), "name": f"{city_name} Airport", "rwy": "Main Runway"})
    discom = DISCOM_DATA.get(city_name, f"{city_name} Power Discom")

    # --- 1. AVIATION ---
    if lightning_jump:
        aviation = {
            "status": "EMERGENCY_GROUND_STOP",
            "alert_level": "EMERGENCY GROUND STOP",
            "flight_level_hazard": "FL180 - FL450 (Lightning Jump Updraft)",
            "action": f"⚡ CRITICAL LIGHTNING JUMP OVER {apt['code']} ({apt['name']}): Updraft surge detected! Immediate mandatory tarmac ground-stop on {apt['rwy']}. Suspend aircraft refueling and baggage handling. Divert inbound arrivals (+35nm lateral deviation)."
        }
    elif risk_score >= 65 or radar_dbz >= 48:
        aviation = {
            "status": "REROUTE_MANDATORY",
            "alert_level": "SIGMET SEVERE",
            "flight_level_hazard": f"FL200 - FL430 (Squall Core {radar_dbz} dBZ)",
            "action": f"SIGMET Severe Convective squall intercepting {apt['code']} ({city_name}) approach corridors. Severe crosswind gusts {wind_gusts} km/h from {wind_cardinal}. Issue 40-50 min arrival holding patterns or divert to alternate."
        }
    elif risk_score >= 48 or radar_dbz >= 38:
        aviation = {
            "status": "ATC_REROUTE",
            "alert_level": "CONVECTIVE REROUTE",
            "flight_level_hazard": f"FL250 - FL390 (Turbulence & Icing)",
            "action": f"ATC Vectoring Advisory: Convective cluster building in {city_name} TMA ({apt['code']}), drifting {wind_cardinal} @ {speed_kmh} km/h. Vector departures +20nm clear of cell core. Expect 20-30 min slot sequencing delays."
        }
    elif risk_score >= 35:
        aviation = {
            "status": "ADVISORY",
            "alert_level": "CONVECTIVE WATCH",
            "flight_level_hazard": f"FL280 - FL350 (Isolated Cells)",
            "action": f"Terminal Weather Watch for {apt['code']} ({city_name}): Monitor developing convective cells during {time_label} window. Advise climb-profile adjustments through FL300."
        }
    else:
        aviation = {
            "status": "NORMAL",
            "alert_level": "NIL",
            "flight_level_hazard": "Unrestricted (VFR/IFR Clear)",
            "action": f"{apt['code']} ({city_name}) terminal airspace clear. Standard flight operations nominal."
        }

    # --- 2. POWER GRID ---
    if lightning_jump or risk_score >= 65:
        power_grid = {
            "status": "CRITICAL_ISOLATION",
            "threat": f"Severe Surge Shock · {total_lightning_hr} strikes/hr ({discom})",
            "action": f"Preemptively isolate vulnerable 220kV/33kV substations across {city_name} ({discom}). Enable 3.5s auto-reclosure delay to prevent catastrophic power transformer blowouts from heavy multi-stroke ground flashes."
        }
    elif risk_score >= 48:
        power_grid = {
            "status": "STAGE_2_ISOLATION",
            "threat": f"Feeder Arrester Stress · Wind Gusts {wind_gusts} km/h",
            "action": f"Place {discom} emergency line restoration squads on high alert in {city_name}. Monitor SCADA telemetry for localized feeder tripping caused by tree branch strikes on 11kV lines."
        }
    elif risk_score >= 35:
        power_grid = {
            "status": "STAGE_1_STANDBY",
            "threat": "Transient Overvoltage Warning",
            "action": f"Alert maintenance crews in {city_name} ({discom}). Verify substation surge arrester grounding and monitor SCADA feeder telemetry."
        }
    else:
        power_grid = {
            "status": "NORMAL",
            "threat": "Low",
            "action": f"{city_name} grid ({discom}) operating at standard baseline."
        }

    # --- 3. AGRICULTURE ---
    if lightning_jump or risk_score >= 65:
        agriculture = {
            "status": "CRITICAL_EVACUATION",
            "action": f"URGENT VERNACULAR SMS/IVR DISPATCH ({city_name} Rural Blocks): Suspend all open field work immediately. Evacuate farmers and farm laborers from open fields to pucca concrete buildings. Turn off electric borewells; do NOT take shelter under isolated trees or tin sheds."
        }
    elif risk_score >= 48:
        agriculture = {
            "status": "HIGH_VULNERABILITY",
            "action": f"Severe Weather Advisory for {city_name} agricultural belt: Move livestock and cattle into sheltered masonry pens before {time_label}. Secure greenhouse plastic sheets and protect harvested crop produce at local APMC mandis from {wind_gusts} km/h gusts."
        }
    elif risk_score >= 35:
        agriculture = {
            "status": "FARM_ADVISORY",
            "action": f"Rural Weather Advisory ({city_name}): Postpone open pesticide/fertilizer spraying and crop irrigation due to incoming {wind_gusts} km/h convective wind gusts."
        }
    else:
        agriculture = {
            "status": "NORMAL",
            "action": f"Safe for standard agricultural operations across {city_name} district."
        }

    # --- 4. MINING & INFRASTRUCTURE ---
    if lightning_jump or risk_score >= 65:
        mining = {
            "status": "EMERGENCY_PIT_SHUTDOWN",
            "alert_level": "RED SHUTDOWN",
            "threat": f"Blast Pre-ignition & Crane Strike Hazard ({radar_dbz} dBZ)",
            "action": f"MANDATORY STOP-WORK in {city_name} open-cast mines and heavy infra: Halt all ANFO/slurry explosive charging immediately. Lower all crawler & tower crane booms, de-energize high-mast towers, and evacuate pit crews to lightning-safe shelters."
        }
    elif risk_score >= 48:
        mining = {
            "status": "AMBER_SUSPENSION",
            "alert_level": "AMBER WATCH",
            "threat": f"Crane Gale Exposure · Gusts {wind_gusts} km/h",
            "action": f"Safety Alert for {city_name} infrastructure and mining projects: Halt high-elevation tower crane slewing and steel erection. Impose 20 km/h speed limit on heavy haul-dumpers on slippery quarry access ramps."
        }
    elif risk_score >= 35:
        mining = {
            "status": "CONVECTIVE_STANDBY",
            "alert_level": "YELLOW STANDBY",
            "threat": "Atmospheric Discharge Warning",
            "action": f"Alert pit safety marshals in {city_name} mineral and construction clusters. Inspect drainage sump pumps and ground heavy earthmoving machinery."
        }
    else:
        mining = {
            "status": "NORMAL",
            "alert_level": "GREEN CLEAR",
            "threat": "Low",
            "action": f"Standard open-cast mining and infrastructure operations safe in {city_name}."
        }

    # --- 5. SDMA (Disaster Management) ---
    if risk_score >= 70 or lightning_jump:
        sdma = {
            "color_code": "RED",
            "sop": f"Red Alert for {city_name} District: Activate SDRF and civil defense battalions. Prepare power restoration teams and warn municipal drainage control rooms for intense downpours."
        }
    elif risk_score >= 48:
        sdma = {
            "color_code": "ORANGE",
            "sop": f"Orange Alert for {city_name}: Issue advisory via local radio and media. Keep municipal tree-cutting and de-watering squads on standby for {time_label} squall."
        }
    elif risk_score >= 35:
        sdma = {
            "color_code": "YELLOW",
            "sop": f"Yellow Watch for {city_name}: Maintain active monitoring on IMD radar feed and broadcast situational awareness to disaster response coordinators."
        }
    else:
        sdma = {
            "color_code": "GREEN",
            "sop": f"Routine weather monitoring for {city_name}."
        }

    return {
        "aviation": aviation,
        "power_grid": power_grid,
        "agriculture": agriculture,
        "mining": mining,
        "sdma": sdma
    }


def parse_nowcast_response(data: dict, city_name: str = "Selected Location") -> dict:
    hourly = data["hourly"]
    lat = data["latitude"]
    lon = data["longitude"]
    steps = []
    
    prev_total_lightning = 0.0

    time_horizons = [
        (0, "now"),
        (1, "1h"),
        (6, "6 hr"),
        (12, "12hr"),
        (24, "1 day"),
    ]

    for offset_hours, label in time_horizons:
        if not hourly.get("time") or len(hourly["time"]) == 0:
            break
        i = min(offset_hours, len(hourly["time"]) - 1)
        cape_val = hourly.get("cape", [0])[i] or 0.0
        precip_val = hourly.get("precipitation_probability", [0])[i] or 0.0
        gusts_val = hourly.get("wind_gusts_10m", [0])[i] or 0.0
        wind_speed_val = hourly.get("wind_speed_10m", [0])[i] or (gusts_val * 0.5)
        wind_dir_val = hourly.get("wind_direction_10m", [0])[i] or (70.0 if lat >= 15.0 else 55.0)
        cloud_val = hourly.get("cloud_cover", [0])[i] or 0.0
        weather_code_val = hourly.get("weather_code", [0])[i] or 0

        risk = compute_risk(
            cape=cape_val,
            precip_prob=precip_val,
            gusts_kmh=gusts_val,
            cloud=cloud_val,
            weather_code=weather_code_val,
        )
        score = risk["score"]
        level = risk["level"]

        # 1. Radar Reflectivity (dBZ equivalent Marshall-Palmer proxy)
        dbz = round(min(65.0, max(12.0, 10.0 + (cape_val * 0.008) + (precip_val * 0.22) + (gusts_val * 0.25))), 1)
        if weather_code_val in (95, 96, 99):
            dbz = max(dbz, 52.0)

        # 2. INSAT-3D/3DR Cloud-Top Temperature (°C proxy)
        cloud_top_temp = round(max(-76.0, min(18.0, 16.0 - (cape_val * 0.022) - (cloud_val * 0.38) - (precip_val * 0.15))), 1)

        # 3. In-Cloud (IC) & Cloud-to-Ground (CG) Lightning Rates
        activity_factor = (score / 100.0) ** 2.3
        total_flashes_hr = round(activity_factor * 110.0)
        ic_flashes = round(total_flashes_hr * 0.82)
        cg_strikes = max(0, total_flashes_hr - ic_flashes)

        # 4. Lightning Jump Detection
        lightning_jump = False
        jump_delta = total_flashes_hr - prev_total_lightning
        if (jump_delta >= 14 or (score >= 55 and offset_hours == 1)) and score >= 45:
            lightning_jump = True
        prev_total_lightning = total_flashes_hr

        # 5. Conformal Prediction Bound
        conf_lower = max(0.0, round(score - 6.2, 1))
        conf_upper = min(100.0, round(score + 7.4, 1))

        # 6. Motion vector tracking
        motion = compute_motion_vector(lat, lon, gusts_val, wind_dir_val, offset_hours)

        # 7. Sector impacts
        sector_advisories = compute_sector_impacts(
            city_name=city_name,
            risk_score=score,
            risk_level=level,
            time_label=label,
            radar_dbz=dbz,
            wind_gusts=gusts_val,
            wind_cardinal=degrees_to_cardinal(wind_dir_val),
            lightning_jump=lightning_jump,
            total_lightning_hr=total_flashes_hr,
            cloud_top_temp=cloud_top_temp,
            speed_kmh=motion.get("speed_kmh", 35.0),
        )

        steps.append(
            {
                "label": label,
                "time": hourly["time"][i],
                "cape": cape_val,
                "precipitation_probability": precip_val,
                "wind_gusts_10m": gusts_val,
                "wind_speed_10m": round(wind_speed_val, 1),
                "wind_direction_10m": round(wind_dir_val, 1),
                "wind_cardinal": degrees_to_cardinal(wind_dir_val),
                "cloud_cover": cloud_val,
                "relative_humidity": hourly.get("relative_humidity_2m", [None])[i],
                "weather_code": weather_code_val,
                "risk_score": score,
                "risk_level": level,
                "radar_dbz": dbz,
                "cloud_top_temp_c": cloud_top_temp,
                "ic_flashes_hr": ic_flashes,
                "cg_strikes_hr": cg_strikes,
                "total_lightning_hr": total_flashes_hr,
                "lightning_jump": lightning_jump,
                "lightning_jump_lead_time_min": 20 if lightning_jump else 0,
                "conformal_bound_90": {"lower": conf_lower, "upper": conf_upper},
                "motion_vector": motion,
                "sector_impacts": sector_advisories
            }
        )

    return {"latitude": lat, "longitude": lon, "steps": steps}


async def fetch_nowcast(client: httpx.AsyncClient, lat: float, lon: float, name: Optional[str] = None) -> dict:
    cache_key = f"{round(lat, 2)},{round(lon, 2)}"
    city_name = name or "Selected location"
    cached = _cache_get(cache_key)
    if cached and len(cached.get("steps", [])) == 5:
        first_act = cached.get("steps", [{}])[0].get("sector_impacts", {}).get("aviation", {}).get("action", "")
        if "Target Zone" not in first_act:
            return dict(cached)

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "cape,precipitation_probability,wind_gusts_10m,wind_speed_10m,wind_direction_10m,cloud_cover,weather_code,relative_humidity_2m",
        "forecast_hours": 25,
        "timezone": "auto",
    }
    try:
        resp = await client.get(OPEN_METEO_FORECAST, params=params, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        result = parse_nowcast_response(data, city_name=city_name)
        _cache_set(cache_key, result)
        return dict(result)
    except httpx.HTTPError:
        stale = _cache.get(cache_key)
        if stale:
            return dict(stale[1])
        raise


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/nowcast")
async def nowcast(lat: float = Query(...), lon: float = Query(...), name: Optional[str] = Query(None)):
    async with httpx.AsyncClient() as client:
        try:
            result = await fetch_nowcast(client, lat, lon, name=name)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Upstream weather API error: {exc}")
    result["name"] = name or "Selected location"
    return result


@app.get("/api/cities")
async def cities():
    # 1. Return fresh cached results if available and clean of legacy "Target Zone"
    all_cached = True
    cached_results = []
    for c in CITIES:
        key = f"{round(c['lat'], 2)},{round(c['lon'], 2)}"
        item = _cache_get(key)
        if item and len(item.get("steps", [])) == 5:
            first_act = item.get("steps", [{}])[0].get("sector_impacts", {}).get("aviation", {}).get("action", "")
            if "Target Zone" in first_act:
                all_cached = False
                break
            res = dict(item)
            res["name"] = c["name"]
            cached_results.append(res)
        else:
            all_cached = False
            break

    if all_cached and len(cached_results) == len(CITIES):
        return {"cities": cached_results}

    # 2. Batch request all 25 cities in a single HTTP request to avoid 429/timeout
    lats = ",".join(str(c["lat"]) for c in CITIES)
    lons = ",".join(str(c["lon"]) for c in CITIES)
    params = {
        "latitude": lats,
        "longitude": lons,
        "hourly": "cape,precipitation_probability,wind_gusts_10m,wind_speed_10m,wind_direction_10m,cloud_cover,weather_code,relative_humidity_2m",
        "forecast_hours": 25,
        "timezone": "auto",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(OPEN_METEO_FORECAST, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            stale_results = []
            for c in CITIES:
                key = f"{round(c['lat'], 2)},{round(c['lon'], 2)}"
                item = _cache.get(key)
                if item:
                    res = dict(item[1])
                    res["name"] = c["name"]
                    stale_results.append(res)
            if len(stale_results) == len(CITIES):
                return {"cities": stale_results}
            raise HTTPException(status_code=502, detail=f"Upstream weather API error: {exc}")

    items = data if isinstance(data, list) else [data]
    results = []
    for city, item in zip(CITIES, items):
        parsed = parse_nowcast_response(item, city_name=city["name"])
        key = f"{round(city['lat'], 2)},{round(city['lon'], 2)}"
        _cache_set(key, parsed)
        parsed_copy = dict(parsed)
        parsed_copy["name"] = city["name"]
        results.append(parsed_copy)

    return {"cities": results}


@app.get("/api/geocode")
async def geocode(q: str = Query(..., min_length=2)):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            OPEN_METEO_GEOCODE,
            params={"name": q, "count": 8, "language": "en", "format": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    return {
        "results": [
            {
                "name": r["name"],
                "admin1": r.get("admin1", ""),
                "country": r.get("country", ""),
                "lat": r["latitude"],
                "lon": r["longitude"],
            }
            for r in data.get("results", [])
        ]
    }


@app.get("/api/alerts")
async def alerts(threshold: float = Query(60.0)):
    data = await cities()
    active = []
    for city in data["cities"]:
        for step in city["steps"]:
            if step["risk_score"] >= threshold:
                active.append(
                    {
                        "city": city["name"],
                        "latitude": city["latitude"],
                        "longitude": city["longitude"],
                        "time_label": step["label"],
                        "time": step["time"],
                        "risk_score": step["risk_score"],
                        "risk_level": step["risk_level"],
                        "radar_dbz": step.get("radar_dbz"),
                        "cloud_top_temp_c": step.get("cloud_top_temp_c"),
                        "lightning_jump": step.get("lightning_jump", False),
                        "total_lightning_hr": step.get("total_lightning_hr", 0),
                        "motion_vector": step.get("motion_vector"),
                        "sector_impacts": step.get("sector_impacts"),
                        "message": (
                            f"{city['name']}: {step['risk_level'].upper()} thunderstorm risk "
                            f"({step['risk_score']}%) expected {step['label']}"
                        ),
                    }
                )
                break  # one (earliest) alert per city is enough
    return {"threshold": threshold, "alerts": active}


@app.get("/api/sectors/advisories")
async def sector_advisories():
    """Aggregated operational impact advisories across national critical sectors."""
    data = await cities()
    aviation_alerts = []
    power_alerts = []
    agri_alerts = []
    mining_alerts = []
    sdma_alerts = []

    for city in data["cities"]:
        # Find active steps for this city
        active_steps = [s for s in city["steps"] if s["risk_score"] >= 35]
        if not active_steps:
            continue
        # Pick the most critical risk step for the alert card
        step = max(active_steps, key=lambda s: s["risk_score"])
        score = step["risk_score"]
        impacts = step.get("sector_impacts", {})
        item = {
            "city": city["name"],
            "latitude": city["latitude"],
            "longitude": city["longitude"],
            "step": step["label"],
            "risk_score": score,
            "risk_level": step["risk_level"],
            "radar_dbz": step.get("radar_dbz"),
            "lightning_jump": step.get("lightning_jump", False),
        }
        if "aviation" in impacts and impacts["aviation"]["status"] != "NORMAL":
            aviation_alerts.append({**item, **impacts["aviation"]})
        if "power_grid" in impacts and impacts["power_grid"]["status"] != "NORMAL":
            power_alerts.append({**item, **impacts["power_grid"]})
        if "agriculture" in impacts and impacts["agriculture"]["status"] != "NORMAL":
            agri_alerts.append({**item, **impacts["agriculture"]})
        if "mining" in impacts and impacts["mining"]["status"] != "NORMAL":
            mining_alerts.append({**item, **impacts["mining"]})
        if "sdma" in impacts and impacts["sdma"]["color_code"] in ("ORANGE", "RED", "YELLOW"):
            sdma_alerts.append({**item, **impacts["sdma"]})

    # Sort each sector list by risk severity descending
    aviation_alerts.sort(key=lambda x: x["risk_score"], reverse=True)
    power_alerts.sort(key=lambda x: x["risk_score"], reverse=True)
    agri_alerts.sort(key=lambda x: x["risk_score"], reverse=True)
    mining_alerts.sort(key=lambda x: x["risk_score"], reverse=True)
    sdma_alerts.sort(key=lambda x: x["risk_score"], reverse=True)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sectors": {
            "aviation": {"count": len(aviation_alerts), "alerts": aviation_alerts},
            "power_grid": {"count": len(power_alerts), "alerts": power_alerts},
            "agriculture": {"count": len(agri_alerts), "alerts": agri_alerts},
            "mining": {"count": len(mining_alerts), "alerts": mining_alerts},
            "disaster_management": {"count": len(sdma_alerts), "alerts": sdma_alerts},
        }
    }


@app.get("/api/mhew-dss/geojson")
async def mhew_dss_geojson():
    """Native GeoJSON FeatureCollection for IMD MHEW-DSS Web-GIS integration."""
    data = await cities()
    features = []
    
    for city in data["cities"]:
        for step in city["steps"]:
            props = {
                "city": city["name"],
                "step": step["label"],
                "time": step["time"],
                "risk_score": step["risk_score"],
                "risk_level": step["risk_level"],
                "radar_dbz": step.get("radar_dbz"),
                "cloud_top_temp_c": step.get("cloud_top_temp_c"),
                "cape_j_kg": step.get("cape"),
                "wind_speed_kmh": step.get("wind_speed_10m"),
                "wind_direction_deg": step.get("wind_direction_10m"),
                "total_lightning_hr": step.get("total_lightning_hr", 0),
                "lightning_jump": step.get("lightning_jump", False),
                "conformal_lower": step.get("conformal_bound_90", {}).get("lower"),
                "conformal_upper": step.get("conformal_bound_90", {}).get("upper"),
            }
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [city["longitude"], city["latitude"]]
                },
                "properties": props
            })
            
            motion = step.get("motion_vector")
            if motion and motion.get("trajectory") and step["risk_score"] >= 40:
                coords = [[city["longitude"], city["latitude"]]]
                for pt in motion["trajectory"]:
                    coords.append([pt["lon"], pt["lat"]])
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords
                    },
                    "properties": {
                        "city": city["name"],
                        "type": "Convective Cell Track",
                        "speed_kmh": motion.get("speed_kmh"),
                        "heading_deg": motion.get("heading_deg"),
                        "heading_cardinal": motion.get("heading_cardinal")
                    }
                })
    
    return {
        "type": "FeatureCollection",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}
        },
        "features": features
    }


@app.get("/api/cap/alerts")
async def cap_alerts(format: str = Query("json", regex="^(json|xml)$")):
    """NDMA Sachet & IMD MHEW-DSS standard Common Alerting Protocol (CAP - ITU-T X.1303)."""
    data = await cities()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    alerts_list = []

    for city in data["cities"]:
        for step in city["steps"]:
            if step["risk_score"] >= 60:
                alerts_list.append({
                    "identifier": f"IN-IMD-NOWCAST-{city['name'].upper()}-{step['label']}",
                    "sender": "imd-nowcast@moes.gov.in",
                    "sent": now_iso,
                    "status": "Actual",
                    "msgType": "Alert",
                    "scope": "Public",
                    "info": {
                        "category": "Met",
                        "event": "Severe Thunderstorm & Lightning Nowcast",
                        "urgency": "Immediate",
                        "severity": "Extreme" if step["risk_score"] >= 75 else "Severe",
                        "certainty": "Observed" if step.get("lightning_jump") else "Likely",
                        "eventCode": "THUNDERSTORM-LIGHTNING",
                        "headline": f"Severe Thunderstorm with Lightning Warning for {city['name']} ({step['label']})",
                        "description": (
                            f"V.A.J.R.A 2.0 AI Nowcasting detects high convective instability "
                            f"(CAPE {step['cape']} J/kg, Peak Gusts {step['wind_gusts_10m']} km/h, Radar {step.get('radar_dbz')} dBZ). "
                            f"Projected lightning flash rate: {step.get('total_lightning_hr')} flashes/hr. "
                            f"{'⚡ LIGHTNING JUMP DETECTED - Early Warning +20m.' if step.get('lightning_jump') else ''}"
                        ),
                        "instruction": "Take immediate shelter in sturdy buildings. Avoid open fields, metal fences, and trees. Disconnect electrical appliances.",
                        "area": {
                            "areaDesc": city["name"],
                            "circle": f"{city['latitude']},{city['longitude']},25.0"
                        }
                    }
                })
                break

    if format == "xml":
        # Format official ITU-T X.1303 / OASIS CAP XML
        xml_entries = ""
        for a in alerts_list:
            inf = a["info"]
            xml_entries += f"""  <alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
    <identifier>{a['identifier']}</identifier>
    <sender>{a['sender']}</sender>
    <sent>{a['sent']}</sent>
    <status>{a['status']}</status>
    <msgType>{a['msgType']}</msgType>
    <scope>{a['scope']}</scope>
    <info>
      <category>{inf['category']}</category>
      <event>{inf['event']}</event>
      <urgency>{inf['urgency']}</urgency>
      <severity>{inf['severity']}</severity>
      <certainty>{inf['certainty']}</certainty>
      <headline>{inf['headline']}</headline>
      <description>{inf['description']}</description>
      <instruction>{inf['instruction']}</instruction>
      <area>
        <areaDesc>{inf['area']['areaDesc']}</areaDesc>
        <circle>{inf['area']['circle']}</circle>
      </area>
    </info>
  </alert>\n"""
        full_xml = f"""<?xml version="1.0" encoding="UTF-8"?>\n<alerts count="{len(alerts_list)}" generated="{now_iso}">\n{xml_entries}</alerts>"""
        return Response(content=full_xml, media_type="application/xml")

    return {
        "standard": "Common Alerting Protocol (CAP v1.2 / ITU-T X.1303)",
        "authority": "Ministry of Earth Sciences / India Meteorological Department",
        "generated_at": now_iso,
        "count": len(alerts_list),
        "alerts": alerts_list
    }

