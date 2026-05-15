"""
Web Search Tool
---------------
Searches the live web for current, time-sensitive information that the
chatbot's static knowledge base cannot answer (e.g. live commodity prices,
recent pest outbreaks in Zambia, fertilizer brand availability, weather
forecasts).

Provider strategy:
  1. Tavily (preferred, requires TAVILY_API_KEY) — purpose-built for LLM
     agents, returns clean structured snippets with relevance scores and
     a synthesized `answer` field. Free tier: 1000 searches/month.
  2. DuckDuckGo via the `duckduckgo-search` library (keyless fallback) —
     zero-config, works offline-ish for demos but lower quality results.
  3. If both fail, returns an empty result structure with `error` set so
     the caller can degrade gracefully.

Both providers' responses are normalized into the same schema so the
chat agent does not need to know which one served the query:

    {
        "query":    str,
        "provider": "tavily" | "duckduckgo" | "none",
        "answer":   str | None,           # synthesized summary (Tavily only)
        "results":  [
            {"title": str, "url": str, "snippet": str, "score": float | None},
            ...
        ],
        "error":    str | None,
    }

A simple in-memory LRU cache with 1-hour TTL avoids spending the user's
Tavily credits (and rate limits on DDG) for repeated identical queries.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60 * 60  # 1 hour
CACHE_MAX = 100

# in-memory cache: query -> (timestamp, response_dict)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _cache_get(key: str) -> dict[str, Any] | None:
    item = _cache.get(key)
    if not item:
        return None
    ts, payload = item
    if time.time() - ts > CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: dict[str, Any]) -> None:
    if len(_cache) >= CACHE_MAX:
        # Drop oldest by timestamp
        oldest = min(_cache.items(), key=lambda kv: kv[1][0])[0]
        _cache.pop(oldest, None)
    _cache[key] = (time.time(), payload)


# ─── Provider 1: Tavily ──────────────────────────────────────────────────────

def _search_tavily(query: str, *, max_results: int = 5) -> dict[str, Any] | None:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        resp = client.search(
            query=query,
            search_depth="basic",
            max_results=max_results,
            include_answer=True,
        )
        results = [
            {
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "snippet": r.get("content", ""),
                "score":   r.get("score"),
            }
            for r in resp.get("results", [])
        ]
        return {
            "query":    query,
            "provider": "tavily",
            "answer":   resp.get("answer"),
            "results":  results,
            "error":    None,
        }
    except ImportError:
        logger.warning("tavily-python not installed; skipping Tavily")
        return None
    except Exception as e:
        logger.warning(f"Tavily search failed for {query!r}: {e!r}")
        return None


# ─── Provider 2: DuckDuckGo (keyless fallback) ───────────────────────────────

def _search_duckduckgo(query: str, *, max_results: int = 5) -> dict[str, Any] | None:
    try:
        # The package was renamed `duckduckgo-search` -> `ddgs` in v6.0+, so try both.
        try:
            from ddgs import DDGS  # type: ignore
        except ImportError:
            from duckduckgo_search import DDGS  # type: ignore

        results: list[dict[str, Any]] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(
                    {
                        "title":   r.get("title", ""),
                        "url":     r.get("href", "") or r.get("url", ""),
                        "snippet": r.get("body", "") or r.get("description", ""),
                        "score":   None,
                    }
                )
        return {
            "query":    query,
            "provider": "duckduckgo",
            "answer":   None,  # DDG does not return a synthesized answer
            "results":  results,
            "error":    None,
        }
    except ImportError:
        logger.warning("duckduckgo-search not installed; cannot fall back")
        return None
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed for {query!r}: {e!r}")
        return None


# ─── Public entrypoint ────────────────────────────────────────────────────────

def web_search(query: str, *, max_results: int = 5) -> dict[str, Any]:
    """Search the web. Returns a normalized dict (see module docstring)."""
    if not query or not query.strip():
        return {
            "query": query, "provider": "none", "answer": None,
            "results": [], "error": "empty_query",
        }

    cache_key = f"{query.strip().lower()}::{max_results}"
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug(f"web_search cache hit: {query!r}")
        return cached

    for provider_fn in (_search_tavily, _search_duckduckgo):
        result = provider_fn(query, max_results=max_results)
        if result and result.get("results"):
            _cache_put(cache_key, result)
            return result

    fallback = {
        "query":    query,
        "provider": "none",
        "answer":   None,
        "results":  [],
        "error":    "all_providers_unavailable",
    }
    return fallback


def clear_cache() -> None:
    _cache.clear()
