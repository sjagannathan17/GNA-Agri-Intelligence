"""
Agronomy KB Lookup (lightweight RAG)
------------------------------------
Looks up the agronomy knowledge base in `data/agronomy_kb.json` using
fuzzy matching over crop / pest / disease / diagnostic-symptom keys.

Why this and not a vector store: the KB is small (tens of entries), the
queries are short, and fuzzy matching with `rapidfuzz` returns useful
results in milliseconds without a model dependency. If the KB grows past
a few hundred entries we'd swap this for sentence-transformers + FAISS,
but for now this is the right complexity tier.

The tool returns a list of matches sorted by relevance, each with the
section it came from (`crops` / `diagnostics` / `gna_model_insights`)
so the chat agent can frame its reply correctly.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_KB_PATH = Path(__file__).parent.parent / "data" / "agronomy_kb.json"
_kb_cache: dict[str, Any] | None = None


def _load_kb() -> dict[str, Any]:
    global _kb_cache
    if _kb_cache is None:
        try:
            with open(_KB_PATH) as f:
                _kb_cache = json.load(f)
        except FileNotFoundError:
            logger.error(f"Agronomy KB not found at {_KB_PATH}")
            _kb_cache = {"crops": {}, "diagnostics": {}, "gna_model_insights": {}}
    return _kb_cache


def _flatten_kb(kb: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the nested KB into a list of searchable docs.

    Each doc has:
      - section:  "crops" | "diagnostics" | "gna_model_insights"
      - key:      original key in the KB
      - searchable_text: comma-separated searchable phrase
      - payload:  the original value (dict or scalar)
    """
    docs: list[dict[str, Any]] = []

    for crop_name, info in kb.get("crops", {}).items():
        searchable_parts = [
            crop_name,
            crop_name.replace("_", " "),
        ]
        for key in ("common_pests", "common_diseases", "key_inputs"):
            for item in info.get(key, []):
                searchable_parts.append(item.replace("_", " "))
        docs.append({
            "section": "crops",
            "key":     crop_name,
            "searchable_text": " ".join(searchable_parts),
            "payload": info,
        })

    for symptom, info in kb.get("diagnostics", {}).items():
        searchable_parts = [
            symptom.replace("_", " "),
            info.get("likely_cause", ""),
            info.get("action", ""),
        ]
        docs.append({
            "section": "diagnostics",
            "key":     symptom,
            "searchable_text": " ".join(searchable_parts),
            "payload": info,
        })

    for insight, value in kb.get("gna_model_insights", {}).items():
        docs.append({
            "section": "gna_model_insights",
            "key":     insight,
            "searchable_text": insight.replace("_", " "),
            "payload": value,
        })

    return docs


def lookup_agronomy(query: str, *, max_results: int = 5, min_score: int = 50) -> dict[str, Any]:
    """Search the agronomy KB.

    Returns:
        {
            "query": str,
            "matches": [
                {"section": str, "key": str, "score": int, "payload": ...},
                ...
            ],
            "available_crops": [str, ...],         # convenience for the agent
            "model_insights":  {...},              # full GNA insights block
        }
    """
    kb = _load_kb()
    docs = _flatten_kb(kb)

    if not query or not query.strip():
        return {
            "query":           query,
            "matches":         [],
            "available_crops": list(kb.get("crops", {}).keys()),
            "model_insights":  kb.get("gna_model_insights", {}),
            "error":           "empty_query",
        }

    try:
        from rapidfuzz import fuzz

        scored = []
        q_norm = query.lower().strip()
        for doc in docs:
            score = max(
                fuzz.token_set_ratio(q_norm, doc["searchable_text"].lower()),
                fuzz.partial_ratio(q_norm, doc["searchable_text"].lower()),
            )
            if score >= min_score:
                scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        matches = [
            {
                "section": doc["section"],
                "key":     doc["key"],
                "score":   score,
                "payload": doc["payload"],
            }
            for score, doc in scored[:max_results]
        ]
    except ImportError:
        logger.warning("rapidfuzz not installed — using substring fallback")
        q_norm = query.lower().strip()
        matches = [
            {
                "section": doc["section"],
                "key":     doc["key"],
                "score":   100 if q_norm in doc["searchable_text"].lower() else 0,
                "payload": doc["payload"],
            }
            for doc in docs
            if q_norm in doc["searchable_text"].lower()
        ][:max_results]

    return {
        "query":           query,
        "matches":         matches,
        "available_crops": list(kb.get("crops", {}).keys()),
        "model_insights":  kb.get("gna_model_insights", {}),
    }
