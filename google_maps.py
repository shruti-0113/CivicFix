"""Google Maps Platform integration for CivicFix (server side).

Why everything lives on the server:
  * the Google API key never reaches the browser bundle
  * every provider (Google / OpenStreetMap) returns the same normalised JSON
  * the map features keep working with zero API keys thanks to free fallbacks

Free fallbacks used when GOOGLE_MAPS_API_KEY is not set:
  * Nominatim - geocoding, place search and nearby civic services
  * OSRM      - routing (directions)

Only the standard library is used, so there is nothing extra to install.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from env_loader import load_dotenv

load_dotenv()

GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
PLACES_V1_URL = "https://places.googleapis.com/v1"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1"

USER_AGENT = "CivicFix/1.0 (civic issue reporting platform)"
TIMEOUT = 9

# OpenStreetMap services ask for a fair-use pace, so requests to the same host
# are spaced out instead of fired in parallel.
_throttle_lock = threading.Lock()
_last_call = {}


def _throttle(host, interval=1.1):
    with _throttle_lock:
        wait = interval - (time.monotonic() - _last_call.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()


def _http_json(url, params=None, headers=None, payload=None, host=None):
    """Return parsed JSON or None when the upstream call fails."""
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        url = f"{url}?{urllib.parse.urlencode(clean)}"

    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)

    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
        method = "POST"

    if host:
        _throttle(host)

    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        try:
            return json.loads(body)
        except ValueError:
            return {"status": "ERROR", "error": f"HTTP {exc.code}"}
    except Exception:
        return None


def is_configured():
    """True when a Google Maps key is available (Google provider is used)."""
    return bool(GOOGLE_MAPS_API_KEY)


def provider_name():
    return "google" if is_configured() else "openstreetmap"


def _google_headers(field_mask):
    return {
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": field_mask,
    }


def haversine_km(lat1, lon1, lat2, lon2):
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lon / 2) ** 2)
    return radius * 2 * math.asin(math.sqrt(a))


def _decode_polyline(encoded, precision=5):
    """Decode Google's encoded polyline into a list of [lat, lng] pairs."""
    points, index, lat, lng = [], 0, 0, 0
    factor = 10 ** precision
    while index < len(encoded):
        for step in range(2):
            shift, result = 0, 0
            while True:
                if index >= len(encoded):
                    return points
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if step == 0:
                lat += delta
            else:
                lng += delta
        points.append([round(lat / factor, 6), round(lng / factor, 6)])
    return points


def _clean_instructions(html):
    if not html:
        return ""
    text = html.replace("<b>", "").replace("</b>", "")
    text = text.replace("<div>", " ").replace("</div>", " ")
    text = text.replace("<span>", "").replace("</span>", "")
    return " ".join(text.split())


def geocode(query, region="in", country=None):
    """Turn free text into coordinates. Returns None when nothing is found."""
    text = (query or "").strip()
    if not text:
        return None

    if is_configured():
        result = _http_json(GEOCODE_URL, {
            "address": text,
            "key": GOOGLE_MAPS_API_KEY,
            "region": region or None,
        })
        if result and result.get("status") == "OK" and result.get("results"):
            top = result["results"][0]
            location = top.get("geometry", {}).get("location", {})
            if "lat" in location and "lng" in location:
                return {
                    "lat": location["lat"],
                    "lng": location["lng"],
                    "address": top.get("formatted_address", text),
                    "place_id": top.get("place_id"),
                    "source": "google",
                }

    result = _http_json(NOMINATIM_SEARCH_URL, {
        "q": text,
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 1,
        "countrycodes": country or "",
    }, host="nominatim")
    if isinstance(result, list) and result:
        top = result[0]
        return {
            "lat": float(top["lat"]),
            "lng": float(top["lon"]),
            "address": top.get("display_name", text),
            "place_id": None,
            "source": "openstreetmap",
        }
    return None


def reverse_geocode(lat, lng):
    """Coordinates to a human readable address."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None

    if is_configured():
        result = _http_json(GEOCODE_URL, {
            "latlng": f"{lat},{lng}",
            "key": GOOGLE_MAPS_API_KEY,
        })
        if result and result.get("status") == "OK" and result.get("results"):
            top = result["results"][0]
            return {
                "lat": lat,
                "lng": lng,
                "address": top.get("formatted_address", ""),
                "source": "google",
            }

    result = _http_json(NOMINATIM_REVERSE_URL, {
        "lat": lat,
        "lon": lng,
        "format": "jsonv2",
        "addressdetails": 1,
    }, host="nominatim")
    if isinstance(result, dict) and result.get("display_name"):
        return {
            "lat": lat,
            "lng": lng,
            "address": result["display_name"],
            "source": "openstreetmap",
        }
    return None


def autocomplete(query, region="in", limit=6):
    """Type-ahead place predictions for the report form."""
    text = (query or "").strip()
    if len(text) < 3:
        return []

    if is_configured():
        result = _http_json(
            f"{PLACES_V1_URL}/places:autocomplete",
            headers=_google_headers(
                "suggestions.placePrediction.placeId,"
                "suggestions.placePrediction.text.text,"
                "suggestions.placePrediction.structuredFormat"
            ),
            payload={
                "input": text,
                "includedRegionCodes": [region] if region else [],
                "languageCode": "en",
            },
        )
        predictions = []
        for suggestion in (result or {}).get("suggestions", [])[:limit]:
            prediction = suggestion.get("placePrediction", {})
            structured = prediction.get("structuredFormat", {}) or {}
            main = (structured.get("mainText", {}) or {}).get("text", "")
            secondary = (structured.get("secondaryText", {}) or {}).get("text", "")
            full_text = (prediction.get("text", {}) or {}).get("text", "")
            description = " — ".join(part for part in (main, secondary) if part) or full_text
            if description:
                predictions.append({
                    "place_id": prediction.get("placeId"),
                    "description": description,
                    "lat": None,
                    "lng": None,
                    "source": "google",
                })
        if predictions:
            return predictions

    result = _http_json(NOMINATIM_SEARCH_URL, {
        "q": text,
        "format": "jsonv2",
        "limit": limit,
        "addressdetails": 1,
    }, host="nominatim")
    predictions = []
    for item in result or []:
        predictions.append({
            "place_id": None,
            "description": item.get("display_name", ""),
            "lat": float(item["lat"]),
            "lng": float(item["lon"]),
            "source": "openstreetmap",
        })
    return predictions


def place_details(place_id):
    """Resolve a Google place id to coordinates and an address."""
    if not place_id:
        return None
    if not is_configured():
        return None

    result = _http_json(
        f"{PLACES_V1_URL}/places/{urllib.parse.quote(str(place_id))}",
        headers=_google_headers("id,location,formattedAddress,displayName"),
    )
    if not result:
        return None
    location = result.get("location", {}) or {}
    if "latitude" not in location:
        return None
    return {
        "lat": location["latitude"],
        "lng": location["longitude"],
        "address": result.get("formattedAddress", ""),
        "place_id": result.get("id"),
        "source": "google",
    }


def directions(origin, destination, mode="driving"):
    """Route between two [lat, lng] points. Returns distance, ETA and a polyline."""
    try:
        o_lat, o_lng = float(origin[0]), float(origin[1])
        d_lat, d_lng = float(destination[0]), float(destination[1])
    except (TypeError, ValueError, IndexError):
        return None

    if is_configured():
        result = _http_json(DIRECTIONS_URL, {
            "origin": f"{o_lat},{o_lng}",
            "destination": f"{d_lat},{d_lng}",
            "mode": mode,
            "key": GOOGLE_MAPS_API_KEY,
        })
        routes = (result or {}).get("routes") or []
        if routes:
            route = routes[0]
            legs = route.get("legs") or []
            leg = legs[0] if legs else {}
            polyline = []
            overview = (route.get("overview_polyline", {}) or {}).get("points", "")
            if overview:
                polyline = _decode_polyline(overview)
            return {
                "distance_km": round((route.get("distance", {}) or {}).get("value", 0) / 1000, 2),
                "duration_min": round((route.get("duration", {}) or {}).get("value", 0) / 60, 1),
                "steps": [_clean_instructions(s.get("html_instructions", ""))
                          for s in (leg.get("steps") or []) if s.get("html_instructions")][:8],
                "polyline": polyline,
                "url": _maps_url(d_lat, d_lng, mode),
                "source": "google",
            }

    result = _http_json(
        f"{OSRM_ROUTE_URL}/{mode}/{o_lng},{o_lat};{d_lng},{d_lat}",
        params={"overview": "full", "geometries": "geojson", "steps": "true"},
        host="osrm",
    )
    routes = (result or {}).get("routes") or []
    if routes:
        route = routes[0]
        geometry = ((route.get("geometry") or {}).get("coordinates")) or []
        polyline = [[round(point[1], 6), round(point[0], 6)] for point in geometry]
        steps = []
        for leg in (route.get("legs") or []):
            for step in (leg.get("steps") or []):
                name = step.get("name") or ""
                instruction = f"Continue on {name}" if name else "Continue ahead"
                steps.append(instruction)
        return {
            "distance_km": round((route.get("distance", 0) or 0) / 1000, 2),
            "duration_min": round((route.get("duration", 0) or 0) / 60, 1),
            "steps": steps[:8],
            "polyline": polyline,
            "url": _maps_url(d_lat, d_lng, mode),
            "source": "openstreetmap",
        }

    straight = haversine_km(o_lat, o_lng, d_lat, d_lng)
    return {
        "distance_km": round(straight, 2),
        "duration_min": round(straight * 3, 1),
        "steps": [],
        "polyline": [[o_lat, o_lng], [d_lat, d_lng]],
        "url": _maps_url(d_lat, d_lng, mode),
        "source": "estimate",
    }


def _maps_url(lat, lng, mode="driving"):
    return (f"https://www.google.com/maps/dir/?api=1&destination={lat},{lng}"
            f"&travelmode={mode}")


NEARBY_KINDS = {
    "hospital": {
        "label": "Hospitals & clinics",
        "google_types": ["hospital", "pharmacy"],
        "search_terms": ["hospital", "clinic"],
    },
    "police": {
        "label": "Police stations",
        "google_types": ["police"],
        "search_terms": ["police station"],
    },
    "fire": {
        "label": "Fire stations",
        "google_types": ["fire_station"],
        "search_terms": ["fire station"],
    },
    "municipal": {
        "label": "Ward & municipal offices",
        "google_types": ["city_hall", "local_government_office", "post_office"],
        "search_terms": ["town hall", "municipal office", "post office"],
    },
    "bus_stop": {
        "label": "Bus stops",
        "google_types": ["bus_stop"],
        "search_terms": ["bus stop"],
    },
    "parking": {
        "label": "Parking lots",
        "google_types": ["parking"],
        "search_terms": ["parking"],
    },
}


def _bbox(lat, lng, radius_m):
    """Bounding box around a point, used for the OpenStreetMap fallback."""
    d_lat = radius_m / 111320.0
    cos_lat = max(0.01, math.cos(math.radians(lat)))
    d_lng = radius_m / (111320.0 * cos_lat)
    return (lng - d_lng, lat + d_lat, lng + d_lng, lat - d_lat)


def nearby_places(lat, lng, kind="hospital", radius=3000, limit=6):
    """Civic services around a point — useful for escalation and emergencies."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return []

    config = NEARBY_KINDS.get(kind, NEARBY_KINDS["hospital"])
    radius = max(200, min(int(radius or 3000), 20000))

    if is_configured():
        result = _http_json(
            f"{PLACES_V1_URL}/places:searchNearby",
            headers=_google_headers(
                "places.id,places.displayName,places.formattedAddress,"
                "places.location,places.types"
            ),
            payload={
                "includedTypes": config["google_types"],
                "locationBias": {
                    "circle": {
                        "center": {"latitude": lat, "longitude": lng},
                        "radius": radius,
                    }
                },
                "maxResultCount": limit,
                "languageCode": "en",
            },
        )
        places = []
        for place in (result or {}).get("places", [])[:limit]:
            location = place.get("location", {}) or {}
            if "latitude" not in location:
                continue
            place_lat, place_lng = location["latitude"], location["longitude"]
            places.append({
                "name": (place.get("displayName", {}) or {}).get("text", "Unnamed place"),
                "address": place.get("formattedAddress", ""),
                "lat": place_lat,
                "lng": place_lng,
                "distance_km": round(haversine_km(lat, lng, place_lat, place_lng), 2),
                "url": _maps_url(place_lat, place_lng),
                "source": "google",
            })
        if places:
            return places

    # OpenStreetMap fallback: Nominatim free-text search restricted to a bbox.
    lon_min, lat_max, lon_max, lat_min = _bbox(lat, lng, radius)
    seen, places = set(), []
    for term in config["search_terms"]:
        result = _http_json(NOMINATIM_SEARCH_URL, {
            "q": term,
            "format": "jsonv2",
            "limit": limit,
            "viewbox": f"{lon_min},{lat_max},{lon_max},{lat_min}",
            "bounded": 1,
            "extratags": 1,
            "addressdetails": 1,
        }, host="nominatim")
        for item in result or []:
            name = item.get("name") or (item.get("namedetails", {}) or {}).get("name")
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                place_lat, place_lng = float(item["lat"]), float(item["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            address = item.get("display_name", "")
            places.append({
                "name": name,
                "address": address,
                "lat": place_lat,
                "lng": place_lng,
                "distance_km": round(haversine_km(lat, lng, place_lat, place_lng), 2),
                "url": _maps_url(place_lat, place_lng),
                "source": "openstreetmap",
            })
    places.sort(key=lambda p: p["distance_km"])
    return places[:limit]


def status():
    """Small payload describing which map backend is live."""
    return {
        "provider": provider_name(),
        "configured": is_configured(),
        "label": "Google Maps Platform" if is_configured() else "OpenStreetMap (free)",
        "features": {
            "geocode": True,
            "autocomplete": True,
            "directions": True,
            "nearby": True,
        },
    }
