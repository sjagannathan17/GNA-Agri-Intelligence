"""
Language Detection
------------------
Detects the language of an incoming farmer message, returning one of
{"en", "bem", "nya"} ("english", "bemba", "nyanja"). The chat agent routes
the system prompt accordingly so replies match the farmer's language.

Strategy:
  1. Try `langdetect` (offline, deterministic). It supports many languages
     including some Bantu varieties via probabilistic n-grams.
  2. Apply a Bemba/Nyanja keyword override — `langdetect` was not trained
     on these specifically, but a small dictionary of high-frequency tokens
     gives us reliable detection on the kinds of short messages farmers send.
  3. Fall back to "en" if confidence is below the threshold.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# --- High-frequency tokens for fast keyword override -------------------------
# Words / phrases drawn from common WhatsApp interactions. Not exhaustive
# linguistically but high precision for short utterances.

_BEMBA_MARKERS = {
    # Greetings / common verbs
    "muli shani", "shani", "natotela", "ndakutotela", "tabaisa",
    "mwapoleni", "mwapoleni mukwai",
    # Agronomic
    "imbeshi", "imbuto", "fya kulima", "ulukuni",
    # Pronouns / possessives
    "yandi", "wandi", "yenu", "yake", "lelo", "ifwe", "abakwasu",
    # Function words
    "panono", "nshingakubomba", "tafyaba",
}

_NYANJA_MARKERS = {
    # Greetings / common verbs
    "muli bwanji", "bwanji", "zikomo", "tikomerezeni", "ndili bwino",
    "moni", "moni bambo", "moni mayi",
    # Agronomic
    "mbewu", "feteleza", "munda",
    # Pronouns / possessives
    "ine", "iwe", "iye", "ife", "inu", "iwo",
    # Function words
    "kapena", "koma", "ndi", "ndipo", "amene", "lero",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _keyword_match(text: str) -> str | None:
    norm = _normalize(text)
    bem_hits = sum(1 for kw in _BEMBA_MARKERS if kw in norm)
    nya_hits = sum(1 for kw in _NYANJA_MARKERS if kw in norm)

    if bem_hits == 0 and nya_hits == 0:
        return None
    if bem_hits > nya_hits:
        return "bem"
    if nya_hits > bem_hits:
        return "nya"
    return None


def detect_language(text: str, *, default: str = "en") -> str:
    """Return one of {"en", "bem", "nya"}.

    Falls back to `default` ("en") for empty / very short text or low confidence.
    """
    if not text or len(text.strip()) < 3:
        return default

    # 1) Bemba / Nyanja keyword override — high precision when it matches
    kw = _keyword_match(text)
    if kw:
        return kw

    # 2) langdetect — offline, deterministic
    try:
        from langdetect import DetectorFactory, detect_langs

        DetectorFactory.seed = 0
        candidates = detect_langs(text)
        if not candidates:
            return default
        top = candidates[0]
        if top.prob < 0.7:
            logger.debug(f"language_detect: low confidence {top}, defaulting to {default}")
            return default
        # langdetect returns ISO codes like 'en', 'sw' (Swahili — close to Bantu).
        # Map Swahili / unknown Bantu to English unless our keyword override caught it.
        if top.lang == "en":
            return "en"
        if top.lang in {"sw", "rw", "ny"}:
            # ny is a real langdetect code for Nyanja/Chichewa
            return "nya" if top.lang in {"sw", "ny"} else default
        return default
    except ImportError:
        logger.warning("langdetect not installed; defaulting to English")
        return default
    except Exception as e:
        logger.debug(f"language_detect failed: {e!r}; defaulting to {default}")
        return default


LANG_NAMES = {
    "en":  "English",
    "bem": "Bemba",
    "nya": "Nyanja (Chichewa)",
    "ton": "Tonga",
    "loz": "Lozi",
    "tum": "Tumbuka",
}


def language_name(code: str) -> str:
    return LANG_NAMES.get((code or "en").lower(), "English")


# Map common UI/display strings → ISO-style codes used by language_name().
# The dashboard sends names like "bemba", "nyanja", "tonga", "lozi"; the
# notebook stores "english". This normalises everything to one short code.
_NAME_TO_CODE = {
    "english":   "en",
    "en":        "en",
    "bemba":     "bem",
    "ichibemba": "bem",
    "bem":       "bem",
    "nyanja":    "nya",
    "chichewa":  "nya",
    "chinyanja": "nya",
    "nya":       "nya",
    "tonga":     "ton",
    "chitonga":  "ton",
    "ton":       "ton",
    "lozi":      "loz",
    "silozi":    "loz",
    "loz":       "loz",
    "tumbuka":   "tum",
    "tum":       "tum",
}


def normalize_language(name_or_code: str | None) -> str:
    """Return the short code (en/bem/nya/ton/loz/tum) for any input form."""
    if not name_or_code:
        return "en"
    return _NAME_TO_CODE.get(name_or_code.strip().lower(), "en")
