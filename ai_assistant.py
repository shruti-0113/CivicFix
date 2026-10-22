"""LLM integration for CivicFix.

Design goals:
  * provider agnostic - OpenAI, Google Gemini, Anthropic, Groq, OpenRouter,
    LM Studio and Ollama (any OpenAI-compatible endpoint) all work
  * zero-config - with no API key the module falls back to a deterministic
    rule engine, so every AI feature still works offline and in a demo
  * no extra dependencies - only the Python standard library is used

Environment variables
---------------------
AI_PROVIDER        auto | openai | gemini | anthropic | groq | custom
OPENAI_API_KEY     + OPENAI_BASE_URL, OPENAI_MODEL
GEMINI_API_KEY     + GEMINI_MODEL
ANTHROPIC_API_KEY  + ANTHROPIC_MODEL
GROQ_API_KEY       + GROQ_MODEL
LLM_API_KEY        + LLM_BASE_URL, LLM_MODEL   (any OpenAI-compatible server)
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

TIMEOUT = 45

AI_CATEGORIES = ["Pothole", "Garbage", "Streetlight", "Water Leak", "Road Damage", "Other"]


# --------------------------------------------------------------------------- #
# Provider discovery
# --------------------------------------------------------------------------- #

def _env(*names):
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _detect_provider():
    """Pick the LLM backend from the environment."""
    requested = os.environ.get("AI_PROVIDER", "auto").strip().lower()

    candidates = [
        ("openai", _env("OPENAI_API_KEY"), "https://api.openai.com/v1", _env("OPENAI_MODEL", "gpt-4o-mini")),
        ("gemini", _env("GEMINI_API_KEY", "GOOGLE_API_KEY"), "https://generativelanguage.googleapis.com/v1beta", _env("GEMINI_MODEL", "gemini-2.0-flash")),
        ("anthropic", _env("ANTHROPIC_API_KEY"), "https://api.anthropic.com/v1", _env("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")),
        ("groq", _env("GROQ_API_KEY"), "https://api.groq.com/openai/v1", _env("GROQ_MODEL", "llama-3.3-70b-versatile")),
        ("custom", _env("LLM_API_KEY"), _env("LLM_BASE_URL", "http://localhost:11434/v1"), _env("LLM_MODEL", "llama3.1")),
    ]

    if requested != "auto":
        for name, key, base, model in candidates:
            if name == requested:
                if not key and requested in ("custom",):
                    key = "local"
                return {"provider": name, "key": key, "base_url": base, "model": model,
                        "live": bool(key)}
        return {"provider": "rules", "key": "", "base_url": "", "model": "rules", "live": False}

    for name, key, base, model in candidates:
        if key:
            return {"provider": name, "key": key, "base_url": base, "model": model, "live": True}

    return {"provider": "rules", "key": "", "base_url": "", "model": "rules", "live": False}


_CONFIG = None


def config():
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = _detect_provider()
    return _CONFIG


def status():
    """Public description of the active AI backend."""
    cfg = config()
    labels = {
        "openai": "OpenAI",
        "gemini": "Google Gemini",
        "anthropic": "Anthropic",
        "groq": "Groq",
        "custom": "Custom / local model",
        "rules": "Built-in rule engine",
    }
    return {
        "provider": cfg["provider"],
        "model": cfg["model"],
        "label": labels.get(cfg["provider"], cfg["provider"]),
        "live": bool(cfg["live"]),
        "mode": "llm" if cfg["live"] else "offline-rules",
    }


# --------------------------------------------------------------------------- #
# Low level HTTP + JSON helpers
# --------------------------------------------------------------------------- #

def _post_json(url, payload, headers=None, timeout=TIMEOUT):
    request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        try:
            return json.loads(body)
        except ValueError:
            return {"error": body[:400]}
    except Exception as exc:  # network down, timeout, bad key, ...
        return {"error": str(exc)}


def _extract_json(text):
    """Pull a JSON object out of a model reply (handles markdown fences)."""
    if not text:
        return None
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.S)
    if fence:
        candidate = fence.group(1).strip()
    try:
        return json.loads(candidate)
    except ValueError:
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start:end + 1])
        except ValueError:
            return None
    return None


def chat_json(system_prompt, user_content, max_tokens=1200, temperature=0.3):
    """Ask the active LLM for a JSON object. Returns None when unavailable."""
    cfg = config()
    if not cfg["live"]:
        return None

    provider = cfg["provider"]
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    if provider == "anthropic":
        result = _post_json(
            f"{cfg['base_url']}/messages",
            {
                "model": cfg["model"],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_content}],
            },
            headers={"x-api-key": cfg["key"], "anthropic-version": "2023-06-01"},
        )
        if not result or "error" in result:
            return None
        blocks = result.get("content") or []
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        return _extract_json(text)

    if provider == "gemini":
        result = _post_json(
            f"{cfg['base_url']}/models/{cfg['model']}:generateContent?key={cfg['key']}",
            {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_content}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                    "responseMimeType": "application/json",
                },
            },
        )
        if not result or "error" in result:
            return None
        candidates = result.get("candidates") or []
        if not candidates:
            return None
        parts = (candidates[0].get("content", {}) or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        return _extract_json(text)

    # OpenAI-compatible (openai, groq, custom, ollama, ...)
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    result = _post_json(
        f"{cfg['base_url']}/chat/completions",
        payload,
        headers={"Authorization": f"Bearer {cfg['key']}"},
    )
    if not result or "error" in result:
        # Some self-hosted servers reject response_format - retry without it.
        payload.pop("response_format", None)
        result = _post_json(
            f"{cfg['base_url']}/chat/completions",
            payload,
            headers={"Authorization": f"Bearer {cfg['key']}"},
        )
        if not result or "error" in result:
            return None
    choices = result.get("choices") or []
    if not choices:
        return None
    text = (choices[0].get("message", {}) or {}).get("content", "")
    return _extract_json(text)


# --------------------------------------------------------------------------- #
# Rule engine - the offline brain
# --------------------------------------------------------------------------- #

CATEGORY_KEYWORDS = {
    "Pothole": [
        "pothole", "pot hole", "potholes", "pit ", "crater", "hole in the road",
        "road pit", "sunken road", "rut", "broken road surface", "road has holes",
        "vehicle damage", "bike fall", "scooter", "two-wheeler",
    ],
    "Road Damage": [
        "crack", "cracked", "cracking", "damaged road", "road damage", "divider",
        "footpath", "foot path", "pavement", "sidewalk", "bridge", "railing",
        "kerb", "curb", "median", "uneven road", "rough road", "black spot",
        "road surface", "broken slab", "damaged footpath", "flyover",
    ],
    "Garbage": [
        "garbage", "waste", "rubbish", "trash", "litter", "dustbin", "garbage bin",
        "dump", "overflowing bin", "sewage", "kitchen waste", "recycle",
        "recyclable", "waste bin", "dirty", "stench", "dead animal", "plastic waste",
    ],
    "Streetlight": [
        "streetlight", "street light", "street lamp", "lamp post", "lamppost",
        "light post", "bulb", "no light", "dark road", "dark street", "unlit",
        "tubelight", "light is not working", "street light is not working",
    ],
    "Water Leak": [
        "water leak", "leak", "leaking", "water pipe", "burst pipe", "flood",
        "flooded", "waterlogging", "water logging", "seepage", "drain",
        "drainage", "sewer", "sewage", "tanker", "water supply", "contamination",
        "overhead tank", "waterlogging", "water is leaking",
    ],
}

HIGH_RISK_WORDS = [
    "deep", "huge", "massive", "dangerous", "severely", "accident", "fatal",
    "electric shock", "live wire", "collapsed", "flooded", "contamination",
    "broken glass", "uncovered", "unattended", "child", "children playing",
    "bus route", "main road", "highway",
]

HIGH_IMPACT_WORDS = [
    "main road", "bus stop", "school", "hospital", "market", "railway station",
    "thousands", "many people", "everyone", "entire", "colony", "daily commuters",
    "everyone uses", "peak hours", "temple", "junction", "footpath",
]

URGENT_WORDS = [
    "emergency", "immediate", "urgent", "asap", "today", "right now", "accident",
    "electric shock", "live wire", "flood", "gas", "fire", "blocked", "getting worse",
    "no water", "no electricity", "danger",
]

LOW_WORDS = [
    "minor", "small", "slightly", "occasionally", "rarely", "side road", "quiet lane",
    "low traffic", "private", "rare", "whenever", "not urgent", "can wait",
    "next week", "small patch",
]

DAY_WORDS = ["every day", "daily", "since days", "for days", "weeks", "since morning",
             "since yesterday", "regularly", "always"]

STOP_WORDS = {"a", "an", "the", "on", "in", "at", "near", "of", "is", "are", "was",
              "and", "for", "to", "with", "my", "our", "their", "has", "have", "it"}


def _normalise(text):
    return re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())


def detect_category(title, description):
    """Score each category against the report text."""
    title_text = _normalise(title)
    body_text = _normalise(description)
    scores = {}
    matched_words = {}

    for category, keywords in CATEGORY_KEYWORDS.items():
        score = 0
        hits = []
        for keyword in keywords:
            key = keyword.strip()
            if key and key in title_text:
                score += 3
                hits.append(key)
            if key and key in body_text:
                score += 1
                hits.append(key)
        if score:
            scores[category] = score
            matched_words[category] = sorted(set(hits))

    if not scores:
        return "Other", 0, []
    category = max(scores, key=lambda c: scores[c])
    best = scores[category]
    total = sum(scores.values()) or 1
    confidence = round(min(0.98, 0.45 + (best / total) * 0.5), 2)
    return category, confidence, matched_words.get(category, [])[:5]


def _score_band(text, keywords, base=3, high_step=1, low_step=-1):
    score = base
    for keyword in keywords:
        if keyword in text:
            score += high_step
    for keyword in LOW_WORDS:
        if keyword in text:
            score += low_step
    return max(1, min(5, score))


def _clean_title(title, description):
    text = " ".join((title or "").split())
    if text:
        return text[:90]
    first = re.split(r"[.!?\n]", (description or "").strip())[0]
    first = " ".join(first.split())
    if not first:
        return "Untitled civic issue"
    return (first[0].upper() + first[1:])[:90]


def rule_triage(title, description, location=""):
    """Deterministic triage used when no LLM is configured."""
    text = _normalise(f"{title} {description}")
    category, confidence, tags = detect_category(title, description)

    severity = _score_band(text, HIGH_RISK_WORDS)
    impact = _score_band(text, HIGH_IMPACT_WORDS)
    urgency = _score_band(text, URGENT_WORDS)

    for keyword in DAY_WORDS:
        if keyword in text:
            impact = min(5, impact + 1)
            urgency = min(5, urgency + 1)

    reasons = []
    if tags:
        reasons.append(f"Matched keywords: {', '.join(tags)}.")
    if any(word in text for word in HIGH_RISK_WORDS):
        reasons.append("Description mentions a safety risk, so severity was raised.")
    if any(word in text for word in HIGH_IMPACT_WORDS):
        reasons.append("A busy or public location was mentioned, so impact was raised.")
    if any(word in text for word in URGENT_WORDS):
        reasons.append("The report reads as urgent, so urgency was raised.")
    if not reasons:
        reasons.append("No strong risk keywords found, so baseline scores were used.")

    return {
        "title": _clean_title(title, description),
        "category": category,
        "severity": severity,
        "impact": impact,
        "urgency": urgency,
        "tags": tags,
        "confidence": confidence,
        "reasoning": " ".join(reasons),
        "location": location,
        "source": "rules",
    }


PLAYBOOKS = {
    "Pothole": {
        "root_cause": "Loss of the road surface and base course, usually caused by water ingress, heavy monsoon traffic and utility cuts that were never properly patched.",
        "steps": [
            "Barricade the spot and drop warning signs - deep potholes are a crash risk at night.",
            "Measure the depth and check whether the road here is waterlogged or the stormwater drain is blocked.",
            "Cut out the loose debris and square the edges so the new patch seats properly.",
            "Fill with cold-mix asphalt, or hot-mix PQC when the patch is larger than about 1 sq m.",
            "Compact with a vibratory roller or hand tamper and check the grade against the surrounding road.",
            "Reopen to traffic only after the patch has cooled and settled, then log it for permanent repair.",
        ],
        "materials": [
            "1-2 bags cold-mix asphalt (about Rs 350-600)",
            "Bitumen priming coat, roughly 2 litres",
            "Edge reinforcement mesh for large patches",
            "Barricade tape, cones and warning signs",
        ],
        "cost": "Rs 600-2,500 per pothole with cold-mix; Rs 3,000-8,000 for a permanent PQC patch.",
        "timeline": "Emergency fill-in: same day to 1 day. Permanent repair: 7-15 days.",
        "department": "Municipal Corporation - Roads & Infrastructure / PWD",
        "prevention": "Quarterly patching drives, a PQC overlay on high-damage roads and drainage repair so water stops seeping under the surface.",
        "escalation": "If it is still open after 48 hours, escalate to the ward councillor and keep the photo and complaint number on record.",
    },
    "Road Damage": {
        "root_cause": "Ageing pavement, poorly compacted patch repairs, or utility trenching that was backfilled without proper compaction testing.",
        "steps": [
            "Photograph the damage from several angles and measure the affected length and width.",
            "Check whether a utility trench was cut nearby - bad backfill is the usual culprit.",
            "Get a written estimate from the PWD for crack sealing or resurfacing.",
            "Use crack-sealing for hairline cracks and full-depth patching where the structure has failed.",
            "Barricade until the repair cures and reopen only once the surface is sound.",
        ],
        "materials": [
            "Bitumen crack sealant",
            "Cold-mix asphalt for patching",
            "Trench PCC and M-sand for backfill",
            "Delineators and speed breakers where needed",
        ],
        "cost": "Rs 150-400 per sq m for crack sealing; Rs 700-1,200 per sq m for resurfacing.",
        "timeline": "Crack sealing: 3-7 days. Full resurfacing: 2-6 weeks.",
        "department": "PWD / Roads Division",
        "prevention": "Audit every trench for compaction, and follow a five-year resurfacing cycle for busy roads.",
        "escalation": "Send the measurements and photos to the PWD chief engineer if the ward office does not respond in a week.",
    },
    "Garbage": {
        "root_cause": "Collection frequency is lower than the waste generated, or there is no door-to-door pickup point at the transfer location.",
        "steps": [
            "Mark the spot and photograph the overflow with something for scale.",
            "Do a one-off deep clean and disinfect the bin and the surrounding area.",
            "Move the bin to a shaded, accessible spot that does not block the road.",
            "Escalate for an extra vehicle or handcart to the transfer station.",
            "Start a daily pickup schedule with a signed resident log so compliance can be proven.",
        ],
        "materials": [
            "Heavy-duty bin liners",
            "Disinfectant and lime powder",
            "Broom, gloves and waste-handling tools",
            "Signage and a dustbin number board",
        ],
        "cost": "Rs 200-1,500 for a deep clean; a monthly collection route is usually Rs 3,000-8,000.",
        "timeline": "Immediate clean: 24-48 hours. Contract or route change: 1-3 weeks.",
        "department": "Municipal Corporation - Solid Waste Management",
        "prevention": "Door-to-door collection with user pays, bin-to-bin daily lifting and community composting for wet waste.",
        "escalation": "If the next two pickups are missed, send the geo-tagged photo to the SWM supervisor and the ward office.",
    },
    "Streetlight": {
        "root_cause": "A failed lamp or switch, an exhausted LED driver, or a disconnected or severed supply line.",
        "steps": [
            "Confirm whether a single lamp or the whole stretch is dark and note the pole number.",
            "Check the control box for a tripped MCB or blown fuse and reset it.",
            "Inspect the LED module and driver - replace the driver or the full luminaire if it is burnt.",
            "Verify the earth connection and tighten the fixture wiring.",
            "Test after dark with a light meter and record the pole in the ward lighting register.",
        ],
        "materials": [
            "LED streetlight luminaire, 40-70 W",
            "MCB or fuse plus a connector block",
            "10-20 m of copper wire for jumpers",
            "Ladder and safety harness (mandatory above 5 m)",
        ],
        "cost": "Rs 2,500-6,000 per luminaire including parts and labour.",
        "timeline": "Single lamp: 2-5 days. Full feeder fault: 1-2 weeks.",
        "department": "Municipal Corporation - Street Lighting / Town Administration",
        "prevention": "Annual lamp health audit, smart-metered LED conversion and monthly feeder checks.",
        "escalation": "Report the pole number to the lighting helpline and escalate to the deputy commissioner if it is dark for more than a week.",
    },
    "Water Leak": {
        "root_cause": "A burst or corroded main or joint, an open sluice valve, or an illegal connection that keeps the network losing pressure.",
        "steps": [
            "Close the nearest valve to stop the loss and check whether neighbouring lines lose pressure too.",
            "Locate the leak - surface seepage, a meter running fast, or a wet patch that keeps growing.",
            "Mark utilities (electricity, gas, telecom) and take the digging permission before excavation.",
            "Replace the failed pipe section or valve with the correct diameter and restore the trench properly.",
            "Flush the line, test for leaks and chlorinate before restoring the supply.",
            "Report it to the water board so supply is restored on schedule and the loss is billed correctly.",
        ],
        "materials": [
            "Ductile iron or UPVC pipe of matching diameter",
            "Sluice or butterfly valve",
            "Jointing gasket and clamps",
            "M-sand and PCC for trench restoration",
        ],
        "cost": "Rs 2,000-25,000 depending on excavation depth and road type.",
        "timeline": "Emergency isolation: within hours. Permanent repair: 1-3 days.",
        "department": "Municipal Corporation - Water Supply / Jal Board",
        "prevention": "Annual leak-detection survey, pressure management and replacing mains that are over 30 years old.",
        "escalation": "Call the 24x7 water helpline with the exact location, then escalate to the executive engineer if supply is not restored in 24 hours.",
    },
    "Other": {
        "root_cause": "The exact cause is not obvious from the report and needs an on-site inspection.",
        "steps": [
            "Photograph the problem from more than one angle and note the time it happens.",
            "Check whether it repeats daily or only at specific hours.",
            "Take the report and photos to the ward office to identify the owning department.",
            "Follow up weekly until the work is assigned and a date is given.",
        ],
        "materials": ["Depends on the confirmed cause"],
        "cost": "To be estimated after inspection.",
        "timeline": "Inspection: 3-7 days.",
        "department": "Ward office / local municipal corporation",
        "prevention": "Add the issue to the ward's published work plan and track it monthly.",
        "escalation": "Escalate to the ward councillor and the commissioner if there is no response in two weeks.",
    },
}


def rule_plan(issue):
    """Offline resolution plan for an issue."""
    category = issue.get("category") or "Other"
    playbook = PLAYBOOKS.get(category, PLAYBOOKS["Other"])
    title = issue.get("title") or "this issue"
    return {
        "summary": f"{title} is a {category.lower()} problem reported at {issue.get('location', 'an unknown location')}. "
                   f"Priority score {issue.get('priority', 0)}/100 with {issue.get('upvotes', 0)} community upvotes.",
        "root_cause": playbook["root_cause"],
        "steps": list(playbook["steps"]),
        "materials": list(playbook["materials"]),
        "estimated_cost": playbook["cost"],
        "timeline": playbook["timeline"],
        "department": playbook["department"],
        "prevention": playbook["prevention"],
        "escalation": playbook["escalation"],
        "source": "rules",
        "model": "rules",
    }


def rule_describe(text, category="Other"):
    """Offline 'write it better' helper for a rough description."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    detected, _, tags = detect_category("", cleaned)
    if category and category != "Other":
        detected = category
    template = (
        f"{cleaned}\n\n"
        f"What is happening: {detected.lower()} reported by a resident.\n"
        f"Impact: it affects everyday users of this stretch, and the problem repeats until it is repaired.\n"
        f"Details worth adding: exact landmark, how long it has been there, who is affected "
        f"(children, elderly, two-wheeler riders), and whether it has caused an accident or a near miss."
    )
    if tags:
        template += f"\nKeywords spotted: {', '.join(tags)}."
    return template


# --------------------------------------------------------------------------- #
# Feature layer - LLM first, rules as the safety net
# --------------------------------------------------------------------------- #

TRIAGE_SYSTEM = """You are the triage engine of CivicFix, a civic issue management platform used in Indian cities.
You classify incoming citizen reports. Reply with a single JSON object and nothing else, using exactly these keys:
{
  "title": "short, specific title (max 90 chars)",
  "category": one of: Pothole, Garbage, Streetlight, Water Leak, Road Damage, Other,
  "severity": integer 1-5 (safety risk if left unaddressed),
  "impact": integer 1-5 (how many people are affected),
  "urgency": integer 1-5 (how soon it must be fixed),
  "tags": ["max 5 short keywords"],
  "confidence": number between 0 and 1,
  "reasoning": "one or two sentences explaining the scores"
}
Be realistic: a minor complaint on a quiet lane is not severity 5. Use the context to judge."""

RESOLVE_SYSTEM = """You are the field operations assistant of CivicFix, a civic issue platform for Indian cities.
Given a reported issue, produce a practical resolution plan. Reply with one JSON object and nothing else:
{
  "summary": "2 sentences describing the issue and why it matters",
  "root_cause": "the most likely technical cause",
  "steps": ["4 to 7 ordered, concrete action steps a municipal crew can follow"],
  "materials": ["3 to 6 materials or equipment with rough quantity"],
  "estimated_cost": "a realistic INR range for India, using the Rs symbol",
  "timeline": "how long it takes for immediate fix and for permanent repair",
  "department": "the exact department or office that owns this work",
  "prevention": "how to stop it recurring",
  "escalation": "who to escalate to and after how long"
}
Keep every step practical and safe. Mention safety gear and permissions where relevant."""

ASSISTANT_SYSTEM = """You are the CivicFix assistant, an AI helper for a civic issue management platform.
The platform stores citizen-reported problems (potholes, garbage, streetlights, water leaks, road damage),
scores them by severity/impact/urgency, tracks status from Reported to In Progress to Resolved,
runs a transparent community fund, and shows everything on a live map.
Use the supplied platform data to answer. Be practical, concise (under 180 words), use plain English and the Rs symbol for money.
Never invent issue IDs or numbers that are not in the data.
Reply with a single JSON object and nothing else:
{"answer": "your reply", "suggestions": ["up to 3 short follow-up questions the user can tap"]}"""

INSIGHTS_SYSTEM = """You are the analytics assistant of CivicFix, a civic issue platform for Indian cities.
Turn the platform statistics into a short actionable briefing for the civic administration.
Reply with one JSON object and nothing else:
{"headline": "one line summary",
 "insights": [{"title": "short title", "detail": "one or two sentences with the actual numbers", "action": "the concrete next step"}],
 "focus_areas": ["2-4 short focus area names"]}
Use only the numbers given to you."""


def triage(title, description, location="", state=""):
    """Classify and score a report."""
    user_content = json.dumps({
        "title": title,
        "description": description,
        "location": location,
        "state": state,
    }, ensure_ascii=False)

    result = chat_json(TRIAGE_SYSTEM, user_content, max_tokens=700)
    if result:
        try:
            category = result.get("category") if result.get("category") in AI_CATEGORIES else None
            severity = int(result.get("severity", 3))
            impact = int(result.get("impact", 3))
            urgency = int(result.get("urgency", 3))
            if not category:
                category, confidence, tags = detect_category(title, description)
            else:
                confidence = float(result.get("confidence", 0.75))
                tags = result.get("tags") or []
            return {
                "title": str(result.get("title") or _clean_title(title, description))[:90],
                "category": category,
                "severity": max(1, min(5, severity)),
                "impact": max(1, min(5, impact)),
                "urgency": max(1, min(5, urgency)),
                "tags": [str(t)[:24] for t in tags][:5],
                "confidence": round(min(1, max(0, confidence)), 2),
                "reasoning": str(result.get("reasoning", "")).strip()[:400],
                "location": location,
                "source": "llm",
                "model": config()["model"],
            }
        except (TypeError, ValueError):
            pass

    result = rule_triage(title, description, location)
    result["model"] = "rules"
    return result


def resolve_plan(issue):
    """Actionable plan to actually fix an issue."""
    user_content = json.dumps(issue, ensure_ascii=False, default=str)
    result = chat_json(RESOLVE_SYSTEM, user_content, max_tokens=1400)
    if result and result.get("steps"):
        plan = {
            "summary": str(result.get("summary", ""))[:600],
            "root_cause": str(result.get("root_cause", ""))[:500],
            "steps": [str(s)[:300] for s in (result.get("steps") or [])][:8],
            "materials": [str(m)[:120] for m in (result.get("materials") or [])][:6],
            "estimated_cost": str(result.get("estimated_cost", ""))[:200],
            "timeline": str(result.get("timeline", ""))[:200],
            "department": str(result.get("department", ""))[:200],
            "prevention": str(result.get("prevention", ""))[:400],
            "escalation": str(result.get("escalation", ""))[:300],
            "source": "llm",
            "model": config()["model"],
        }
        if plan["steps"]:
            return plan

    plan = rule_plan(issue)
    plan["model"] = "rules"
    return plan


def assist(message, history=None, context=None):
    """Ground the assistant in real platform data, then answer."""
    context = context or {}
    history = history or []

    payload = {
        "platform_data": context,
        "conversation": [
            {"role": h.get("role", "user"), "content": str(h.get("content", ""))[:800]}
            for h in history[-6:]
        ],
        "user_message": message,
    }
    result = chat_json(ASSISTANT_SYSTEM, json.dumps(payload, ensure_ascii=False, default=str),
                       max_tokens=900)
    if result and result.get("answer"):
        return {
            "answer": str(result["answer"])[:2500],
            "suggestions": [str(s)[:80] for s in (result.get("suggestions") or [])][:3],
            "source": "llm",
            "model": config()["model"],
        }

    answer, suggestions = rule_assistant(message, context)
    return {
        "answer": answer,
        "suggestions": suggestions,
        "source": "rules",
        "model": "rules",
    }


def rule_assistant(message, context):
    """Deterministic assistant that answers from the real platform data."""
    text = _normalise(message)
    stats = context.get("stats", {}) or {}
    funds = context.get("funds", {}) or {}
    top_issues = context.get("top_issues", []) or []
    mentioned = context.get("mentioned_issue")
    category = context.get("mentioned_category")
    playbook = context.get("playbook")

    def line(issue):
        return (f"#{issue.get('id')} - {issue.get('title')} ({issue.get('category')}, "
                f"priority {issue.get('priority')}/100, {issue.get('status')})")

    if any(word in text for word in ["hello", "hi ", "hey", "help", "what can you"]):
        return (
            "I am the CivicFix assistant. I can:\n"
            "- pick the next issue your team should fix\n"
            "- explain step by step how to fix a pothole, leak, garbage or streetlight\n"
            "- summarise the current state of the platform and the community fund\n"
            "- draft a report and score a new issue\n\n"
            "Try: \"What should we fix first?\"",
            ["What should we fix first?", "How do I fix a pothole?", "Show me the stats"],
        )

    if any(word in text for word in ["first", "next", "priorit", "start with", "suggest"]):
        if not top_issues:
            return ("There are no open issues right now - report the first one and I will rank it.",
                    ["Show me the stats"])
        picks = "\n".join(f"{index + 1}. {line(issue)}" for index, issue in enumerate(top_issues[:3]))
        lead = top_issues[0]
        steps = PLAYBOOKS.get(lead.get("category"), PLAYBOOKS["Other"])["steps"][:2]
        moves = " ".join(step.rstrip(".") for step in steps)
        return (
            f"Start here:\n{picks}\n\n"
            f"Issue #{lead.get('id')} scores highest because of its severity, impact and urgency. "
            f"First moves: {moves}.",
            [f"How do I fix a {lead.get('category', '').lower()}?", f"Show issue #{lead.get('id')}"],
        )

    if any(word in text for word in ["fund", "money", "donat", "budget", "balance", "rupee", "rs "]):
        return (
            f"Community fund: Rs {funds.get('total_raised', 0):,.0f} raised from "
            f"{funds.get('donation_count', 0)} contributors, Rs {funds.get('total_spent', 0):,.0f} spent, "
            f"Rs {funds.get('balance', 0):,.0f} available.\n\n"
            "Best use right now: fund the materials for the top priority issue first, then post the "
            "receipt in the expense ledger so the balance stays auditable.",
            ["What should we fix first?", "Show me the stats"],
        )

    if any(word in text for word in ["stat", "total", "how many", "overview", "summary", "dashboard"]):
        return (
            f"CivicFix snapshot: {stats.get('total', 0)} reports, {stats.get('reported', 0)} reported, "
            f"{stats.get('in_progress', 0)} in progress, {stats.get('resolved', 0)} resolved "
            f"({stats.get('resolution_rate', 0)}% resolution rate). "
            f"Average priority {stats.get('average_priority', 0)}/100.\n\n"
            f"Most reported categories: {', '.join(context.get('top_categories', [])[:3]) or 'none yet'}.",
            ["What should we fix first?", "Show me the fund balance"],
        )

    if mentioned:
        return (
            f"Issue #{mentioned.get('id')}: {mentioned.get('title')} ({mentioned.get('category')}).\n"
            f"Status: {mentioned.get('status')} | Priority {mentioned.get('priority')}/100 | "
            f"Upvotes {mentioned.get('upvotes', 0)} | Reported {mentioned.get('created_at', '')[:10]}.\n\n"
            f"Recommended department: {PLAYBOOKS.get(mentioned.get('category'), PLAYBOOKS['Other'])['department']}.",
            [f"How do I fix a {mentioned.get('category', '').lower()}?", "What should we fix first?"],
        )

    if playbook:
        steps = "\n".join(f"{index + 1}. {step}" for index, step in enumerate(playbook["steps"][:4]))
        return (
            f"Here is how to fix a {playbook.get('label', 'issue').lower()} problem:\n{steps}\n\n"
            f"Materials: {', '.join(playbook['materials'][:3])}. "
            f"Cost: {playbook['cost'].rstrip('.')}. "
            f"Owner: {playbook['department'].rstrip('.')}.",
            ["What should we fix first?", "Show me the stats"],
        )

    if category:
        issues = context.get("category_issues", []) or []
        listing = "\n".join(f"- {line(issue)}" for issue in issues[:5]) or "- no open reports in this category"
        return (
            f"{category} reports ({len(issues)} open):\n{listing}\n\n"
            f"Typical owner: {PLAYBOOKS.get(category, PLAYBOOKS['Other'])['department']}.",
            [f"How do I fix a {category.lower()}?", "What should we fix first?"],
        )

    return (
        "Here is where CivicFix stands: "
        f"{stats.get('total', 0)} issues logged, {stats.get('resolved', 0)} resolved, "
        f"Rs {funds.get('balance', 0):,.0f} in the community fund.\n\n"
        "Ask me what to fix first, how to fix a specific problem, or for a status summary.",
        ["What should we fix first?", "How do I fix a pothole?", "Show me the stats"],
    )


def insights(context):
    """City level briefing built from platform analytics."""
    result = chat_json(INSIGHTS_SYSTEM, json.dumps(context, ensure_ascii=False, default=str),
                       max_tokens=1200)
    if result and result.get("insights"):
        items = []
        for item in (result.get("insights") or [])[:5]:
            if not isinstance(item, dict):
                continue
            items.append({
                "title": str(item.get("title", "Insight"))[:80],
                "detail": str(item.get("detail", ""))[:400],
                "action": str(item.get("action", ""))[:300],
            })
        if items:
            return {
                "headline": str(result.get("headline", ""))[:200],
                "insights": items,
                "focus_areas": [str(f)[:60] for f in (result.get("focus_areas") or [])][:5],
                "source": "llm",
                "model": config()["model"],
            }
    return rule_insights(context)


def rule_insights(context):
    """Offline briefing computed directly from the numbers."""
    stats = context.get("stats", {}) or {}
    analytics = context.get("analytics", {}) or {}
    funds = context.get("funds", {}) or {}
    categories = analytics.get("categories", []) or []
    distribution = analytics.get("priority_distribution", {}) or {}

    total = stats.get("total", 0) or 0
    insights = []

    if categories:
        top = categories[0]
        share = round((top.get("count", 0) / total) * 100) if total else 0
        insights.append({
            "title": f"{top.get('category')} dominates the queue",
            "detail": f"{top.get('count', 0)} of {total} reports ({share}%) are {top.get('category').lower()} issues.",
            "action": f"Schedule a dedicated {top.get('category').lower()} repair drive and stock the matching materials.",
        })
        if len(categories) > 1:
            second = categories[1]
            insights.append({
                "title": f"Pair {top.get('category')} with {second.get('category')}",
                "detail": f"{second.get('count', 0)} {second.get('category').lower()} reports share the same crews, so they compete for the same manpower.",
                "action": "Bundle both categories into one weekly field route to halve the travel time.",
            })

    critical = distribution.get("critical") or 0
    if critical:
        insights.append({
            "title": f"{critical} critical issues need attention",
            "detail": "Critical issues score 80 or above on the severity, impact and urgency model.",
            "action": "Move these to the top of the field list and barricade the site the same day.",
        })

    unresolved = (stats.get("reported", 0) or 0) + (stats.get("in_progress", 0) or 0)
    if unresolved:
        insights.append({
            "title": f"{unresolved} issues are still open",
            "detail": f"The resolution rate is {stats.get('resolution_rate', 0)}% and the average priority is {stats.get('average_priority', 0)}/100.",
            "action": "Publish a weekly status update per issue so residents can see progress.",
        })

    balance = funds.get("balance", 0) or 0
    if balance:
        insights.append({
            "title": f"Rs {balance:,.0f} of the community fund is unspent",
            "detail": f"Rs {funds.get('total_raised', 0):,.0f} has been raised and Rs {funds.get('total_spent', 0):,.0f} spent so far.",
            "action": "Buy the materials for the top priority issue and record the receipt in the expense ledger.",
        })

    if not insights:
        insights.append({
            "title": "Not enough data yet",
            "detail": "Report a few civic issues and this panel will turn them into an action plan.",
            "action": "Start with the report form and let the AI score the severity for you.",
        })

    return {
        "headline": f"{total} reports tracked - {stats.get('resolved', 0)} resolved, {unresolved} still open",
        "insights": insights,
        "focus_areas": [c.get("category", "") for c in categories[:3]],
        "source": "rules",
        "model": "rules",
    }


def describe(text, category="Other"):
    """Turn a rough description into a clear, structured report."""
    result = chat_json(
        "You rewrite citizen civic issue descriptions into a clear, factual paragraph of 2-3 sentences. "
        "Keep every detail the citizen gave, do not invent facts, and reply with JSON: {\"description\": \"...\"}.",
        json.dumps({"description": text, "category": category}, ensure_ascii=False),
        max_tokens=500,
    )
    if result and result.get("description"):
        return {
            "description": str(result["description"])[:1200],
            "source": "llm",
            "model": config()["model"],
        }
    return {
        "description": rule_describe(text, category),
        "source": "rules",
        "model": "rules",
    }
