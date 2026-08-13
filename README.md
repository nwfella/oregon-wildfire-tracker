# Oregon Wildfire Tracker 🔥

Live wildfire tracker for the state of Oregon — active fires, daily perimeters, containment,
air quality, red-flag warnings, and smoke outlooks. **Fully static**: every data point is baked
into `index.html` server-side, so the page works even where corporate IT blocks `fetch`/XHR.

**Live:** https://nwfella.github.io/oregon-wildfire-tracker/

## Features

- **Hero map, top and center** — full-width canvas map of Oregon on page load; **⛶ maximizes it to full screen** (native Fullscreen API + CSS fallback, Esc/✕ to exit)
- **Mobile-first** — 60vh touch map, pinch-zoom + drag pan, 40px tap targets, swipeable alert strip, responsive grid lists; everything below the fold stacks cleanly
- **County burn-heat choropleth** — counties shaded by active fire acreage
- **Active fires** — 97 incidents live from the NIFC/WFIGS feed: size, containment, cause,
  personnel, structures lost, complexity, discovery & last-report times
- **Daily perimeters** — orange fire boundaries, pulse-highlighted when a fire is selected
- **Air quality** — PM2.5 readings from 100+ Oregon monitors (OpenAQ mirror), EPA AQI +
  category colors, worst-first ranking
- **NWS alerts** — red flag warnings, fire weather watches, evacuations, air quality alerts,
  heat warnings (color-coded swipe strip + list)
- **Oregon Smoke Blog** — latest smoke outlook posts
- **No-JS fallback** — static table of the top fires renders with JavaScript disabled
- **Zero runtime network calls** — a cron refreshes the snapshot every 30 minutes

## Data sources (all public, no API keys)

| Data | Source |
|---|---|
| Incidents | Esri Live Feeds `USA_Wildfires_v1` (NIFC/WFIGS mirror) |
| Perimeters | Esri `Wildfire_aggregated_v1` (daily fire perimeters) |
| Air quality | Esri OpenAQ mirror (PM2.5 latest readings) |
| Alerts | NWS `api.weather.gov` (area=OR) |
| Smoke | Oregon Smoke Information Blog (RSS) |
| Counties | BLM OR County Boundaries (Oregon statewide framework) |

## How it works

```
scripts/collect.py (cron, every 30 min)
  ├─ fetch incidents / perimeters / AQI / alerts / smoke (parallel, keyless)
  ├─ normalize + simplify geometry (Douglas-Peucker: 11 MB counties → 117 KB)
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

## Stats snapshot (Aug 2026 fire season)

97 active fires, ~2.26M acres burning, 6 red-flag warnings, worst AQI 394 (Hazardous).

## License

MIT — see [LICENSE](LICENSE).
