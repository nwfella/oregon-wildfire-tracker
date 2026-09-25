# Oregon Wildfire Tracker 🔥

Live wildfire tracker for the state of Oregon — active fires, daily perimeters, containment,
air quality, red-flag warnings, and smoke outlooks. **Fully static**: every data point is baked
into `index.html` server-side, so the page works even where corporate IT blocks `fetch`/XHR.

**Live:** https://nwfella.github.io/oregon-wildfire-tracker/

## Features

- **Hero map, top and center** — full-width canvas map of Oregon on page load; **⛶ maximizes it to full screen** (native Fullscreen API + CSS fallback, Esc/✕ to exit)
- **5 color themes** — Ember (default), Forest, Ocean, Magma, Daybreak; theme picker in the header, saved to localStorage, the canvas map recolorizes to match
- **Legend = layer filters** — tap any legend row (active fires, perimeters, county burn heat, AQI monitors) to hide/show that layer on the map; multi-select; "show all" reset; choices remembered
- **Collapsible legend** — the **−** button minimizes it to a small "Legend" pill (tap to bring it back; auto-collapsed on phones, where it used to be hidden entirely)
- **Dismissable alerts** — collapse the whole alert strip to a "🚩 N alerts" pill (auto-collapsed on phones for extra map room), or ✕ away individual alerts you don't care about; both choices remembered in localStorage
- **Mobile-first** — 60vh touch map, pinch-zoom + drag pan, 40px tap targets, swipeable alert strip, responsive grid lists; everything below the fold stacks cleanly
- **County burn-heat choropleth** — counties shaded by active fire acreage
- **Active fires** — 97 incidents live from the NIFC/WFIGS feed: size, containment, cause,
  personnel, structures lost, complexity, discovery & last-report times
- **Anchored popup details** — tap a fire on the map and a popup bubble appears right next to the marker (arrow pointing at it, flips below near the top edge, follows while panning/zooming); select a fire or AQI monitor from the lists below and the same detail card appears inline at the top of the rail — full details visible from either surface, no scrolling required
- **Daily perimeters** — orange fire boundaries, pulse-highlighted when a fire is selected
- **Evacuation zones** — Genasys Protect GO/SET/READY/order polygons overlaid on the map
  (pulse-highlighted at GO/order), level counts in the legend, and a warning banner when
  any zone is at GO NOW / evacuation order; the **EVAC tab** lists active zones grouped by
  county (GO first) — tap a row or a map polygon for details, zoom in for on-map status labels
- **Air quality** — PM2.5 readings from 100+ Oregon monitors (OpenAQ mirror), EPA AQI +
  category colors, worst-first ranking
- **NWS alerts** — red flag warnings, fire weather watches, evacuations, air quality alerts,
  heat warnings (color-coded swipe strip + list)
- **Oregon Smoke Blog** — latest smoke outlook posts
- **No-JS fallback** — static table of the top fires renders with JavaScript disabled
- **Zero runtime network calls** — a cron refreshes the snapshot once a day (08:00 PT)

## Data sources (all public, no API keys)

| Data | Source |
|---|---|
| Incidents | Esri Live Feeds `USA_Wildfires_v1` (NIFC/WFIGS mirror) |
| Perimeters | Esri `Wildfire_aggregated_v1` (daily fire perimeters) |
| Evacuation zones | Genasys Protect (Zonehaven EVAC) public GeoServer WFS, non-Normal zones only |
| Air quality | Esri OpenAQ mirror (PM2.5 latest readings) |
| Alerts | NWS `api.weather.gov` (area=OR) |
| Smoke | Oregon Smoke Information Blog (RSS) |
| Counties | BLM OR County Boundaries (Oregon statewide framework) |

## Data attribution

Evacuation zone data is courtesy of **Genasys Protect** (protect.genasys.com) and the
participating Oregon counties that publish through it. The tracker queries Genasys's public
WFS server-side (no API keys) and bakes the snapshot; it is not an official Genasys product.
Zone statuses change quickly — **always verify with local officials** before acting on them.

## How it works

```
scripts/collect.py (via scripts/publish.py, cron daily 08:00 PT)
  ├─ fetch incidents / perimeters / evac zones / AQI / alerts / smoke (parallel, keyless)
  ├─ normalize + simplify geometry (Douglas-Peucker: 11 MB counties → 117 KB; evac zones filtered to non-Normal via CQL, reprojected 3857→4326)
  ├─ compute stats + county burn heat + EPA AQI
  └─ bake inline JSON into index.html via template.html markers
        → git commit + push → GitHub Pages serves the static snapshot
```

- `template.html` — editable source with `/*__OR_FIRE_START__*/`…`/*__OR_FIRE_END__*/` markers
- `index.html` — generated, fully self-contained (~360 KB), committed
- `scripts/geo.py` — county/perimeter simplification (`python scripts/geo.py <src.geojson> <out.json>`)
- `scripts/publish.py` — cron wrapper: bake → commit only if changed → push (silent when nothing new)
- `assets/counties.json` — cached simplified Oregon counties

### Local refresh

```bash
python scripts/collect.py    # fetches + bakes index.html
```

## Refresh cadence

**Once a day at 08:00 PT.** One Hermes cron job runs `scripts/publish.py` (via
`oregon-fire-publish.py`, job `6369ea550765`); it bakes the snapshot, commits + pushes only when
the material data changed, and stays silent otherwise. The job is script-only (`no_agent`), so it
consumes no AI tokens.

> **Changed 2026-09-25:** this used to run **3×/day** (`0 0,8,16 * * *` — every 8 hours). Git
> history showed the bake produced a material change 2–3×/day, so the third run was usually a
> no-op. It is now a single daily 08:00 PT run.
>
> **Freshness caveat:** with a 24-hour cycle, *every* baked layer can be up to a day old —
> including the time-sensitive ones, the Genasys evacuation zones and the NWS red-flag warnings.
> Those upstream sources update far faster than the bake. Treat the page as a snapshot, not a
> live feed, and **verify evac status with local officials** before acting on it. Cadence is a
> one-line cron change if a faster cycle is ever needed during a peak (e.g. 08:00 + 20:00).

## Stats snapshot (Aug 2026 fire season)

97 active fires, ~2.26M acres burning, 6 red-flag warnings, worst AQI 394 (Hazardous),
229 active evacuation zones (81 GO NOW / order, 69 set, 78 ready).

## License

MIT — see [LICENSE](LICENSE).
