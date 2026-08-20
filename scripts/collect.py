#!/usr/bin/env python3
"""Oregon Wildfire Tracker — data collector & static baker.

Fetches live wildfire data (keyless public APIs), normalizes it, and bakes a
fully static snapshot into index.html (zero runtime network calls — the IT-safe
pattern: the page must render even where fetch/XHR is blocked).

Sources:
  - Incidents:  Esri Live Feeds USA_Wildfires_v1 (NIFC/WFIGS mirror)
  - Perimeters: Esri Wildfire_aggregated_v1 layer 1 (daily fire perimeters)
  - Air quality: Esri OpenAQ mirror (PM2.5 latest readings)
  - Alerts:     NWS api.weather.gov (area=OR), fire-relevant events only
  - Smoke:      Oregon Smoke Information Blog RSS
  - Counties:   cached simplified GeoJSON (assets/counties.json, see geo.py)

Usage:  python scripts/collect.py
"""
import json
import math
import os
import re
import sys
import time
import datetime
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from geo import dp_simplify, rings_from_geom  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
OR_BBOX = "-124.8,41.9,-116.3,46.5"
FIRES_URL = ("https://services9.arcgis.com/RHVPKKiFTONKtxq3/arcgis/rest/services/"
             "USA_Wildfires_v1/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson"
             "&geometry=-124.8%2C41.9%2C-116.3%2C46.5&geometryType=esriGeometryEnvelope&inSR=4326&outSR=4326"
             "&resultRecordCount=600")
PERIM_URL = ("https://services9.arcgis.com/RHVPKKiFTONKtxq3/arcgis/rest/services/"
             "Wildfire_aggregated_v1/FeatureServer/1/query?where=1%3D1&outFields=*&f=geojson"
             "&geometry=-124.8%2C41.9%2C-116.3%2C46.5&geometryType=esriGeometryEnvelope&inSR=4326&outSR=4326"
             "&resultRecordCount=600")
AQI_URL = ("https://services9.arcgis.com/RHVPKKiFTONKtxq3/arcgis/rest/services/"
           "Air_Quality_PM25_Latest_Results/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson"
           "&geometry=-124.8%2C41.9%2C-116.3%2C46.5&geometryType=esriGeometryEnvelope&inSR=4326&outSR=4326"
           "&resultRecordCount=400")
NWS_URL = "https://api.weather.gov/alerts/active?area=OR"
SMOKE_URL = "https://oregonsmoke.blogspot.com/feeds/posts/default?alt=rss"

# Genasys Protect (Zonehaven EVAC) — public GeoServer WFS of county evacuation zones.
# The full layer is ~80 MB for the Oregon bbox, so we filter server-side with CQL to
# non-Normal zones only (~1.5 MB). Geometry is EPSG:3857; we reproject to 4326.
# Data courtesy of Genasys Protect / participating counties (footer attribution).
EVAC_CQL = "BBOX(geom,-125,41.9,-116.3,46.5,'EPSG:4326') AND status <> 'Normal'"
EVAC_URL = ("https://cdngeospatialcei.zonehaven.com/geoserver/zonehavenv2/wfs?service=WFS"
            "&version=1.1.0&request=GetFeature&typeName=zonehavenv2:evacuation_zone"
            "&outputFormat=application/json&CQL_FILTER=" + urllib.parse.quote(EVAC_CQL, safe=""))

ALERT_EVENTS = {
    "Red Flag Warning": 1, "Fire Weather Watch": 2, "Evacuation": 3, "Evacuation Order": 3,
    "Evacuation Warning": 3, "Air Quality Alert": 4, "Excessive Heat Warning": 5,
    "Heat Advisory": 6, "High Wind Warning": 7, "Wind Advisory": 8, "Fire Warning": 1,
    "Flash Flood Warning": 7, "Flood Warning": 8, "Flood Watch": 9, "Severe Thunderstorm Warning": 9,
}
ALERT_CATEGORIES = {
    "Fire Warning": "Fire Warning", "Red Flag Warning": "Red Flag Warning", "Fire Weather Watch": "Fire Weather Watch",
    "Evacuation Order": "Evacuation", "Evacuation Warning": "Evacuation", "Evacuation": "Evacuation",
    "Air Quality Alert": "Air Quality Alert",
}
KEEP_UNKNOWN = True  # keep unexpected events but rank them lowest


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_json(url, timeout=30):
    return json.loads(fetch(url, timeout).decode("utf-8", "replace"))


def epoch_to_iso(ms):
    if not ms:
        return None
    try:
        return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def pm25_to_aqi(pm):
    """EPA PM2.5 breakpoints → AQI + category + color."""
    bp = [(0.0, 12.0, 0, 50), (12.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
          (55.5, 150.4, 151, 200), (150.5, 250.4, 201, 300), (250.5, 500.4, 301, 500)]
    for lo, hi, alo, ahi in bp:
        if lo <= pm <= hi:
            aqi = int(round((ahi - alo) / (hi - lo) * (pm - lo) + alo))
            break
    else:
        aqi = min(999, int(pm * 2))
    if aqi <= 50:
        return aqi, "Good", "#00e400"
    if aqi <= 100:
        return aqi, "Moderate", "#ffff00"
    if aqi <= 150:
        return aqi, "USG", "#ff7e00"
    if aqi <= 200:
        return aqi, "Unhealthy", "#ff0000"
    if aqi <= 300:
        return aqi, "Very Unhealthy", "#8f3f97"
    return aqi, "Hazardous", "#7e0023"


# ---------------------------------------------------------------- incidents
def get_incidents():
    g = fetch_json(FIRES_URL)
    feats = g.get("features", [])
    out = []
    seen = set()
    for f in feats:
        a = f.get("properties") or f.get("attributes") or {}
        if a.get("POOState") != "US-OR":
            continue
        uid = a.get("UniqueFireIdentifier") or a.get("IrwinID") or str(a.get("OBJECTID"))
        if uid in seen:
            continue
        seen.add(uid)
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates")
        if not coords or len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        acres = a.get("CalculatedAcres") or a.get("DailyAcres") or 0
        struc = (a.get("ResidencesDestroyed") or 0) + (a.get("OtherStructuresDestroyed") or 0)
        out.append({
            "id": uid,
            "n": a.get("IncidentName") or "Unnamed fire",
            "cnty": (a.get("POOCounty") or "").strip() or None,
            "acres": round(acres, 1),
            "cont": a.get("PercentContained"),
            "cause": a.get("FireCause") or None,
            "kind": a.get("IncidentTypeKind") or a.get("IncidentTypeCategory") or None,
            "per": a.get("TotalIncidentPersonnel"),
            "struc": struc or None,
            "inj": a.get("Injuries") or None,
            "fat": a.get("Fatalities") or None,
            "comp": a.get("FireMgmtComplexity") or None,
            "mgmt": a.get("IncidentManagementOrganization") or None,
            "fuel": a.get("PredominantFuelGroup") or None,
            "disp": epoch_to_iso(a.get("FireDiscoveryDateTime")),
            "rep": epoch_to_iso(a.get("ICS209ReportDateTime")),
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "irwin": a.get("IrwinID"),
        })
    out.sort(key=lambda i: -(i["acres"] or 0))
    return out


# ---------------------------------------------------------------- perimeters
def get_perimeters(incidents):
    irwin_ids = set(i.get("irwin") for i in incidents if i.get("irwin"))
    g = fetch_json(PERIM_URL)
    now = datetime.datetime.now(datetime.timezone.utc)
    out = []
    used_names = set()
    for f in g.get("features", []):
        a = f.get("properties") or {}
        dcur = a.get("DateCurrent")
        keep = False
        if a.get("IRWINID") in irwin_ids:
            keep = True
        elif dcur:
            try:
                dt = datetime.datetime.fromtimestamp(dcur / 1000, tz=datetime.timezone.utc)
                if (now - dt).days <= 30:
                    keep = True
            except (ValueError, OSError, OverflowError):
                keep = False
        if not keep:
            continue
        rings = rings_from_geom(f.get("geometry"))
        simp = []
        for r in rings:
            s = dp_simplify(r, 0.004)
            if len(s) >= 8:
                # cap per-ring points
                if len(s) > 2500:
                    step = len(s) // 2500
                    s = s[::step] + [s[-1]]
                simp.append(s)
        if not simp:
            continue
        name = (a.get("IncidentName") or "").strip()
        if not name:
            continue
        # dedupe by name: keep the most recent
        date_s = epoch_to_iso(dcur)
        if name in used_names:
            continue
        used_names.add(name)
        out.append({"n": name, "d": date_s, "acres": round(a.get("GISAcres") or 0, 0), "p": simp})
    # cap total points to keep the HTML lean
    total = sum(len(r) for o in out for r in o["p"])
    if total > 40000:
        factor = 40000 / total
        for o in out:
            o["p"] = [r[::max(1, int(1 / factor))] + [r[-1]] for r in o["p"]]
    out.sort(key=lambda o: -o["acres"])
    return out


# ---------------------------------------------------------------- air quality
def get_aqi():
    g = fetch_json(AQI_URL)
    out = []
    for f in g.get("features", []):
        a = f.get("properties") or {}
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates")
        if not coords or len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        if not (-124.7 <= lon <= -116.4 and 41.95 <= lat <= 46.3):
            continue  # strict Oregon bounds (bbox pulls WA/ID too)
        v = a.get("value")
        if v is None or a.get("parameter") != "pm25":
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v < 0 or v > 2000:
            continue
        aqi, cat, col = pm25_to_aqi(v)
        out.append({
            "c": (a.get("city") or "Unknown").strip(),
            "l": (a.get("location") or a.get("city") or "").strip(),
            "v": round(v, 1),
            "aqi": aqi,
            "cat": cat,
            "col": col,
            "t": a.get("lastUpdated"),
            "lat": round(lat, 4),
            "lon": round(lon, 4),
        })
    # dedupe by location name, keep newest
    by_name = {}
    for o in out:
        key = o["l"] or o["c"]
        if key not in by_name or (o.get("t") or "") > (by_name[key].get("t") or ""):
            by_name[key] = o
    return sorted(by_name.values(), key=lambda o: -o["v"])


# ---------------------------------------------------------------- evac zones (Genasys Protect)
EVAC_LEVEL_COLORS = {4: "#c9184a", 3: "#ff3b30", 2: "#ff8c1a", 1: "#ffd24d", 0: "#8b93a7"}


def evac_level(status):
    """Map a Genasys status string to a numeric level (4=order, 3=GO, 2=SET, 1=READY, 0=other)."""
    s = (status or "").lower()
    if "level 3" in s or "go now" in s:
        return 3
    if "order" in s:
        return 4
    if "level 2" in s or "be set" in s:
        return 2
    if "level 1" in s or "be ready" in s:
        return 1
    return 0


def webmerc_to_lonlat(x, y):
    """EPSG:3857 (meters) -> EPSG:4326 (degrees)."""
    lon = x / (math.pi * 6378137.0) * 180.0
    lat = math.degrees(2.0 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2.0)
    return (round(lon, 6), round(lat, 6))


def get_evac():
    """Fetch active (non-Normal) Genasys evacuation zones for Oregon, as simplified 4326 rings."""
    g = fetch_json(EVAC_URL)
    out = []
    for f in g.get("features", []):
        a = f.get("properties") or {}
        st = (a.get("status") or "").strip()
        lv = evac_level(st)
        rings = rings_from_geom(f.get("geometry"))  # raw rings are EPSG:3857
        simp = []
        for r in rings:
            rr = [webmerc_to_lonlat(x, y) for x, y in r]
            s = dp_simplify(rr, 0.002)
            if len(s) >= 4:
                if len(s) > 800:  # cap per-ring points
                    step = len(s) // 800
                    s = s[::step] + [s[-1]]
                simp.append(s)
        if not simp:
            continue
        out.append({
            "id": a.get("id") or a.get("identifer") or None,
            "n": (a.get("evacuation_zone") or a.get("commonly_known_as") or "").strip() or "Evacuation zone",
            "st": st, "lv": lv, "p": simp,
        })
    # cap total points to keep the baked HTML lean
    total = sum(len(r) for o in out for r in o["p"])
    if total > 25000:
        factor = 25000 / total
        for o in out:
            o["p"] = [r[::max(1, int(1 / factor))] + [r[-1]] for r in o["p"]]
    out.sort(key=lambda o: -o["lv"])
    return out


def build_static_evac_line(evac):
    """No-JS fallback line for the noscript block."""
    if not evac:
        return '<p style="margin:10px 0; color:#5c6478;">No active evacuation zones reported.</p>'
    go = sum(1 for z in evac if z["lv"] >= 3)
    st = sum(1 for z in evac if z["lv"] == 2)
    rd = sum(1 for z in evac if z["lv"] == 1)
    parts = []
    if go:
        parts.append(f'<b style="color:#ff3b30;">{go} zone{"s" if go != 1 else ""} at GO NOW / order</b>')
    if st:
        parts.append(f"{st} be set")
    if rd:
        parts.append(f"{rd} be ready")
    return ('<p style="margin:10px 0; color:#8b93a7;">⚠ Evacuation zones (Genasys Protect): '
            + ", ".join(parts)
            + '. <a href="https://protect.genasys.com/" style="color:#ff6b1a;">Verify with Genasys Protect</a>.</p>')


# ---------------------------------------------------------------- alerts
def get_alerts():
    d = fetch_json(NWS_URL)
    out = []
    for f in d.get("features", []):
        p = f.get("properties") or {}
        event = p.get("event") or ""
        rank = ALERT_EVENTS.get(event)
        if rank is None and not KEEP_UNKNOWN:
            continue
        if rank is None:
            rank = 99
        out.append({
            "e": ALERT_CATEGORIES.get(event, event),
            "raw": event,
            "h": p.get("headline") or event,
            "a": p.get("areaDesc") or "",
            "sev": p.get("severity") or "",
            "on": p.get("onset"),
            "ex": p.get("expires"),
            "d": (p.get("description") or "")[:400],
            "url": "https://api.weather.gov/alerts/" + (f.get("id", "").rsplit("/", 1)[-1]),
            "rank": rank,
        })
    out.sort(key=lambda o: o["rank"])
    return out


# ---------------------------------------------------------------- smoke blog
def get_smoke():
    data = fetch(SMOKE_URL)
    root = ET.fromstring(data)
    out = []

    # RSS 2.0 shape: <rss><channel><item><title><link><pubDate><description>
    items = root.findall(".//item")
    if items:
        for item in items[:3]:
            t = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = item.findtext("pubDate") or ""
            desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
            desc = re.sub(r"\s+", " ", desc).strip()
            out.append({"t": t, "p": pub, "s": desc[:400], "u": link})
        return out

    # Atom shape: <feed><entry><title><link><published><summary>
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("a:entry", ns)[:3]:
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        link = ""
        for l in entry.findall("a:link", ns):
            if l.get("rel") == "alternate" or l.get("rel") is None:
                link = l.get("href", "")
                break
        pub = entry.findtext("a:published", default="", namespaces=ns)
        summary = entry.findtext("a:summary", default="", namespaces=ns) or ""
        summary = re.sub(r"<[^>]+>", " ", summary)
        summary = re.sub(r"\s+", " ", summary).strip()
        out.append({"t": title, "p": pub, "s": summary[:400], "u": link})
    return out


# ---------------------------------------------------------------- cities
CITIES = [
    ("Portland", 45.515, -122.678, 1), ("Salem", 44.943, -123.033, 1), ("Eugene", 44.052, -123.087, 1),
    ("Bend", 44.058, -121.315, 1), ("Medford", 42.326, -122.875, 1), ("Klamath Falls", 42.225, -121.782, 1),
    ("Pendleton", 45.672, -118.789, 1), ("Corvallis", 44.565, -123.262, 0), ("Albany", 44.637, -123.106, 0),
    ("Springfield", 44.046, -123.022, 0), ("Gresham", 45.500, -122.431, 0), ("Hillsboro", 45.523, -122.990, 0),
    ("Beaverton", 45.487, -122.804, 0), ("McMinnville", 45.210, -123.198, 0), ("The Dalles", 45.594, -121.179, 0),
    ("Hood River", 45.705, -121.521, 0), ("Astoria", 46.188, -123.831, 0), ("Newport", 44.637, -124.054, 0),
    ("Coos Bay", 43.366, -124.213, 0), ("Brookings", 42.053, -124.284, 0), ("Grants Pass", 42.439, -123.328, 0),
    ("Roseburg", 43.216, -123.342, 0), ("Ashland", 42.195, -122.709, 0), ("Redmond", 44.273, -121.173, 0),
    ("Prineville", 44.300, -120.834, 0), ("La Grande", 45.324, -118.088, 0), ("Baker City", 44.775, -117.834, 0),
    ("Ontario", 44.027, -116.963, 0), ("Hermiston", 45.840, -119.289, 0), ("Burns", 43.586, -119.054, 0),
    ("John Day", 44.416, -118.953, 0), ("Lakeview", 42.189, -120.346, 0), ("Tillamook", 45.456, -123.844, 0),
    ("Florence", 43.983, -124.099, 0), ("Gold Beach", 42.407, -124.421, 0), ("Cottage Grove", 43.798, -123.059, 0),
    ("Lincoln City", 44.958, -124.018, 0), ("Seaside", 45.993, -123.922, 0), ("Forest Grove", 45.520, -123.111, 0),
]


def build_counties(incidents):
    with open(os.path.join(ROOT, "assets", "counties.json"), encoding="utf-8") as f:
        data = json.load(f)
    burn = {}
    for i in incidents:
        if i.get("cnty"):
            key = i["cnty"].strip().lower()
            burn[key] = burn.get(key, 0) + (i["acres"] or 0)
    out = []
    for c in data["counties"]:
        out.append({"n": c["name"], "b": round(burn.get(c["name"].lower(), 0), 0), "p": c["polys"]})
    return out


def build_static_fires_table(incidents):
    rows = []
    for i in incidents[:12]:
        cont = i["cont"] if i["cont"] is not None else "—"
        rows.append(
            f'<tr><td style="padding:6px;border:1px solid #242a38;">{i["n"]}</td>'
            f'<td style="padding:6px;border:1px solid #242a38;">{i["cnty"] or "—"}</td>'
            f'<td style="padding:6px;border:1px solid #242a38;">{int(i["acres"]):,}</td>'
            f'<td style="padding:6px;border:1px solid #242a38;">{cont}%</td>'
            f'<td style="padding:6px;border:1px solid #242a38;">{i["cause"] or "—"}</td></tr>')
    return "\n".join(rows)


def main():
    t0 = time.time()
    print("== Oregon Wildfire Tracker bake ==")
    warnings = []

    try:
        incidents = get_incidents()
        print(f"  incidents: {len(incidents)}")
        if not incidents:
            print("FATAL: zero Oregon incidents — refusing to bake; keeping previous index.html")
            sys.exit(1)
    except Exception as e:
        print(f"FATAL: incidents fetch failed: {e}")
        sys.exit(1)

    try:
        perimeters = get_perimeters(incidents)
        print(f"  perimeters: {len(perimeters)}")
    except Exception as e:
        warnings.append(f"perimeters: {e}")
        perimeters = []

    try:
        aqi = get_aqi()
        print(f"  aqi monitors: {len(aqi)}")
    except Exception as e:
        warnings.append(f"aqi: {e}")
        aqi = []

    try:
        alerts = get_alerts()
        red_flags = sum(1 for a in alerts if "Red Flag" in a["raw"] or "Fire Warning" in a["raw"])
        print(f"  alerts: {len(alerts)} ({red_flags} red flag/fire warnings)")
    except Exception as e:
        warnings.append(f"alerts: {e}")
        alerts = []
        red_flags = 0

    try:
        smoke = get_smoke()
        print(f"  smoke posts: {len(smoke)}")
    except Exception as e:
        warnings.append(f"smoke: {e}")
        smoke = []

    try:
        evac = get_evac()
        evac_go = sum(1 for z in evac if z["lv"] == 3)
        evac_order = sum(1 for z in evac if z["lv"] == 4)
        evac_set = sum(1 for z in evac if z["lv"] == 2)
        evac_ready = sum(1 for z in evac if z["lv"] == 1)
        print(f"  evac zones: {len(evac)} active "
              f"({evac_order + evac_go} go/order, {evac_set} set, {evac_ready} ready)")
    except Exception as e:
        warnings.append(f"evac: {e}")
        evac = []
        evac_go = evac_order = evac_set = evac_ready = 0

    counties = build_counties(incidents)
    print(f"  counties: {len(counties)}")

    with open(os.path.join(ROOT, "assets", "oregon_outline.json"), encoding="utf-8") as f:
        outline = json.load(f)["rings"]
    print(f"  state outline rings: {len(outline)}")

    # ---- stats
    total_acres = sum(i["acres"] or 0 for i in incidents)
    conts = [i["cont"] for i in incidents if i["cont"] is not None]
    avg_cont = round(sum(conts) / len(conts)) if conts else None
    total_per = sum(i["per"] or 0 for i in incidents)
    now = datetime.datetime.now(datetime.timezone.utc)
    new24 = sum(1 for i in incidents if i["disp"] and
                (now - datetime.datetime.fromisoformat(i["disp"])).total_seconds() < 86400)
    worst = aqi[0] if aqi else None
    stats = {
        "fires": len(incidents), "acres": round(total_acres), "avgCont": avg_cont,
        "per": total_per, "new24": new24, "redFlags": red_flags,
        "worstAqi": {"city": worst["c"], "aqi": worst["aqi"], "cat": worst["cat"]} if worst else None,
        "evac": {"go": evac_go, "order": evac_order, "set": evac_set, "ready": evac_ready, "total": len(evac)},
    }

    data = {
        "updated": now_iso(),
        "stats": stats,
        "incidents": incidents,
        "perimeters": perimeters,
        "evac": evac,
        "aqi": aqi,
        "alerts": [{"e": a["e"], "h": a["h"], "a": a["a"], "sev": a["sev"],
                    "on": a["on"], "ex": a["ex"], "d": a["d"], "url": a["url"]} for a in alerts],
        "smoke": smoke,
        "counties": counties,
        "outline": outline,
        "cities": [{"n": n, "lat": la, "lon": lo, "major": m} for n, la, lo, m in CITIES],
    }

    # ---- bake
    with open(os.path.join(ROOT, "template.html"), "rb") as f:
        html = f.read()

    payload = ("\nconst OR_FIRE_DATA = " +
               json.dumps(data, separators=(",", ":")).replace("<", "\\u003c") + ";\n").encode("utf-8")

    START = b"/*__OR_FIRE_START__*/"
    END = b"/*__OR_FIRE_END__*/"
    s = html.find(START)
    e = html.find(END)
    if s == -1 or e == -1 or e <= s:
        print("FATAL: bake markers not found in template.html")
        sys.exit(1)
    baked = html[:s + len(START)] + payload + html[e:]

    static_rows = build_static_fires_table(incidents).encode("utf-8")
    m = b"<!--__STATIC_FIRES__-->"
    mi = baked.find(m)
    if mi != -1:
        baked = baked[:mi] + static_rows + baked[mi + len(m):]

    evac_line = build_static_evac_line(evac).encode("utf-8")
    m2 = b"<!--__STATIC_EVAC__-->"
    mi2 = baked.find(m2)
    if mi2 != -1:
        baked = baked[:mi2] + evac_line + baked[mi2 + len(m2):]

    out_path = os.path.join(ROOT, "index.html")
    with open(out_path, "wb") as f:
        f.write(baked)

    # ---- data artifacts
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "timestamp.json"), "w") as f:
        json.dump({"updated": data["updated"]}, f, separators=(",", ":"))
    with open(os.path.join(ROOT, "data", "snapshot.json"), "w") as f:
        json.dump(data, f, separators=(",", ":"))

    kb = os.path.getsize(out_path) / 1024
    print(f"  baked index.html: {kb:.0f} KB in {time.time() - t0:.1f}s")
    if warnings:
        print("  WARN:", "; ".join(warnings))
    print(f"  top fires: {', '.join(i['n'] for i in incidents[:5])}")
    if worst:
        print(f"  worst AQI: {worst['c']} — {worst['aqi']} ({worst['cat']})")
    print("OK")


if __name__ == "__main__":
    main()
