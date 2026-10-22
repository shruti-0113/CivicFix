# CivicFix — Real-World Civic Issue Resolution Platform

[![CI & Deploy](https://github.com/shruti-0113/CivicFix/actions/workflows/ci.yml/badge.svg)](https://github.com/shruti-0113/CivicFix/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

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
- Google Maps Platform search, directions and nearby civic services (with an OpenStreetMap fallback)
- LLM assistant: auto-classify reports, rewrite descriptions, generate field repair plans, city insights and grounded chat
- 28 automated API tests running in CI on every push

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

## Configuration

Every setting is optional. Copy the template and fill in only what you need:

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

CivicFix reads `.env` at startup through `env_loader.py` (standard library only, no `python-dotenv` needed). Real environment variables always win, so a hosting platform can override anything set locally, and the file is resolved next to the project so it works from any directory. `.env` is git-ignored. Set `CIVICFIX_SKIP_DOTENV=1` to ignore the file completely — the test suite does this so it always runs against the offline rule engine.

| Variable | Purpose |
|---|---|
| `GOOGLE_MAPS_API_KEY` | Enables the Google Maps Platform. Empty → free OpenStreetMap fallback |
| `UPI_ID` | Your UPI ID for the "Pay via UPI" contribution links |
| `AI_PROVIDER` | `auto` · `openai` · `gemini` · `anthropic` · `groq` · `custom` |
| `OPENAI_API_KEY` | OpenAI key (`OPENAI_BASE_URL`, `OPENAI_MODEL` optional) |
| `GEMINI_API_KEY` | Google Gemini key (`GEMINI_MODEL` optional) |
| `ANTHROPIC_API_KEY` | Anthropic key (`ANTHROPIC_MODEL` optional) |
| `GROQ_API_KEY` | Groq key (`GROQ_MODEL` optional) |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | Any OpenAI-compatible server |
| `PORT` / `FLASK_DEBUG` | Server port and debug mode (set `FLASK_DEBUG=0` in production) |

## Tests

```bash
python -m unittest discover -s tests -v
```

28 tests cover the issue lifecycle, filters and search, the community fund ledger, the AI triage/describe/resolve/insights/assistant endpoints and the Maps request validation. The suite uses a throw-away SQLite database, needs no API keys and makes no network calls — it also asserts that every database connection is closed.

GitHub Actions runs the same suite on every push and pull request against Python 3.10 and 3.12.

## Deploy online (free)

### Render (easiest)
1. On render.com → New → Blueprint → select this repo (uses `render.yaml`)
2. Add `GOOGLE_MAPS_API_KEY` / your AI keys under Environment variables
3. Done — you get `https://civicfix-xxxx.onrender.com`, and **every push to `main` redeploys automatically**

Note: Render's free tier has ephemeral disk — the database resets on restarts.

### Auto-deploy from GitHub Actions
`.github/workflows/ci.yml` tests on every push and then deploys `main` to Render using the official `render-oss/render-deploy` action. Add two repository secrets to switch it on:

| Secret | Where to get it |
|---|---|
| `RENDER_API_KEY` | render.com → Account Settings → API Keys |
| `RENDER_SERVICE_ID` | Your service → Settings → the Service ID at the bottom |

Until `RENDER_API_KEY` is set the deploy job is skipped and CI stays green.

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
