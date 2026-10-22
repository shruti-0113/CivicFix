"""Tiny `.env` reader — standard library only, no `python-dotenv` dependency.

CivicFix stays dependency-light on purpose: the Maps and AI integrations talk
to their providers over plain `urllib`. This module keeps the same spirit for
configuration.

Rules:
- Real environment variables always win, so a hosting platform can override
  anything set locally.
- The file is looked up next to this module, so the app behaves the same no
  matter which directory it is started from.
- Set `CIVICFIX_SKIP_DOTENV=1` to ignore the file entirely (used by the test
  suite so it always runs against the offline rule engine).
"""

from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = os.path.join(BASE_DIR, ".env")


def load_dotenv(path: str | None = None) -> dict:
    """Load KEY=VALUE pairs from a `.env` file into `os.environ`.

    Returns the mapping that was applied. Existing environment variables are
    never overwritten.
    """
    if os.environ.get("CIVICFIX_SKIP_DOTENV", "").strip():
        return {}

    target = path or DOTENV_PATH
    if not os.path.isfile(target):
        return {}

    applied = {}
    with open(target, "r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
                applied[key] = value
    return applied
