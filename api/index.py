"""Vercel serverless entrypoint.

Vercel calls this file for every request. It simply hands the WSGI app over
to the platform, which wraps it in a WSGI request handler.

The project root is added to `sys.path` so `from app import app` resolves
regardless of the working directory Vercel uses.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import app  # noqa: E402

__all__ = ["app"]
