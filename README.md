# CivicFix — Real-World Civic Issue Resolution Platform

CivicFix is a portfolio-grade full-stack web app for reporting and prioritizing local civic problems such as potholes, garbage, streetlights and water leaks. It now ships with **Google Maps Platform integration** and a built-in **AI assistant** that classifies reports, writes field repair plans and answers questions from live platform data.

## Features
- Issue reporting with category, description, location and image URL
- Priority scoring based on severity, affected people and urgency
- Search/filter/sort issues
- Status workflow: Reported → In Progress → Resolved
- Dashboard with live statistics
- Responsive UI
- REST API with Flask + SQLite
- Seed data for instant demo

## Maps integration (Google Maps Platform)
`google_maps.py` wraps the Maps Platform on the **server side** — the API key never reaches the browser bundle.

| Feature | Endpoint | What it does |
|---|---|---|
| Area search | `GET /api/maps/geocode?q=` | Address/landmark → coordinates |
| Place autocomplete | `GET /api/maps/autocomplete?q=` | Type-ahead in the report form |
| Reverse geocode | `GET /api/maps/reverse?lat=&lng=` | Pin → readable address |
| Directions | `GET /api/maps/directions?origin=lat,lng&destination=lat,lng` | Distance, ETA and a route polyline |
| Nearby services | `GET /api/maps/nearby?lat=&lng=&kind=` | Hospitals, police, fire, municipal offices, bus stops, parking |

**Enable these in Google Cloud** when you set a key: Maps JavaScript API · Geocoding API · Places API (New) · Directions API.

**No key needed.** With `GOOGLE_MAPS_API_KEY` unset, CivicFix automatically falls back to free OpenStreetMap services (Nominatim for search and nearby places, OSRM for routing) — the map, search, directions and nearby features all keep working.

```bash
export GOOGLE_MAPS_API_KEY="your-key-here"   # Windows: $env:GOOGLE_MAPS_API_KEY="your-key-here"
```

## AI assistant (LLM powered)
`ai_assistant.py` talks to any LLM you configure and falls back to a deterministic rule engine when no key is set, so every AI feature works offline.

- **🤖 Auto-fill** — classify a new report and score severity / impact / urgency, plus near-duplicate detection.
- **✨ Improve description** — turn a rough note into a clear, structured report.
- **🤖 Solve** — a step-by-step field repair plan for any issue: root cause, steps, materials, cost, timeline, owning department, prevention and escalation path. Plans are saved against the issue.
- **AI insights** — a city-level briefing built from live analytics ("Pothole dominates the queue → schedule a patch drive").
- **Assistant chat** — grounded in the database: "What should we fix first?", "How do I fix a streetlight?", "What is the fund balance?", "Status of issue #3".

Supported providers (auto-detected from the first key found):

| Provider | Environment variables |
|---|---|
| OpenAI | `OPENAI_API_KEY`, optional `OPENAI_BASE_URL`, `OPENAI_MODEL` (default `gpt-4o-mini`) |
| Google Gemini | `GEMINI_API_KEY` / `GOOGLE_API_KEY`, optional `GEMINI_MODEL` (default `gemini-2.0-flash`) |
| Anthropic | `ANTHROPIC_API_KEY`, optional `ANTHROPIC_MODEL` |
| Groq | `GROQ_API_KEY`, optional `GROQ_MODEL` |
| Any OpenAI-compatible server (OpenRouter, LM Studio, Ollama) | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` |
| Force one with | `AI_PROVIDER=openai\|gemini\|anthropic\|groq\|custom\|auto` |

```bash
export OPENAI_API_KEY="sk-..."
export GEMINI_API_KEY="..."
```

## Run locally

```bash
python -m venv venv
# Windows: venv\Scripts\activate
# macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

No extra Python packages are needed — the Maps and AI integrations use only the standard library.

## Deploy online (free)

### Render (easiest)
1. Push this folder to a GitHub repo
2. On render.com → New → Blueprint → select the repo (uses `render.yaml`)
3. Done — you get `https://civicfix-xxxx.onrender.com`
Note: Render's free tier has ephemeral disk — the database resets on restarts.

### PythonAnywhere (persistent free SQLite, recommended for this project)
1. Upload/clone the project on pythonanywhere.com
2. Web tab → Manual configuration → Python 3.10+, set source dir
3. WSGI file: `from app import app as application`
4. Add your keys under Environment variables
SQLite lives permanently — no data loss.

## Temporary demo link from your own PC
Any tool that tunnels localhost works, e.g. cloudflared:
`cloudflared tunnel --url http://127.0.0.1:5000`
Link stays live while your PC and the command run.

## Resume bullet
Built a full-stack civic issue management platform using Flask, SQLite, JavaScript and REST APIs that automatically prioritizes community problems using a severity/impact/urgency scoring model, integrates the Google Maps Platform (geocoding, autocomplete, directions, nearby civic services) with a free OpenStreetMap fallback, and ships an LLM-powered assistant that auto-classifies reports, generates field repair plans and answers grounded questions from live platform data.

## API reference
| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/issues` | List/search/filter issues |
| POST | `/api/issues` | Report an issue |
| PATCH | `/api/issues/<id>` | Update status |
| POST | `/api/issues/<id>/upvote` | Upvote |
| DELETE | `/api/issues/<id>` | Delete |
| GET/POST | `/api/issues/<id>/comments` | Discussion thread |
| GET | `/api/stats`, `/api/analytics` | Dashboard data |
| GET/POST | `/api/donations`, `/api/expenses`, `/api/funds` | Community fund ledger |
| GET | `/api/maps/*` | Maps Platform proxy (geocode, autocomplete, reverse, place, directions, nearby) |
| GET | `/api/ai/status` | Which AI backend is live |
| POST | `/api/ai/triage` | Classify + score a report |
| POST | `/api/ai/describe` | Rewrite a rough description |
| POST | `/api/ai/resolve` | Field repair plan for an issue |
| GET | `/api/ai/insights` | City-level insights from live data |
| POST | `/api/ai/assistant` | Grounded assistant chat |
