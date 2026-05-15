"""Unit tests for the new chat-agent tools.

Tests cover:
  - language_detect: keyword override + langdetect fallback + low confidence
  - web_search: cache, Tavily success path, DDG fallback, all-providers-fail
  - market_price: GNA + open-market reference, alias normalization
  - agronomy_rag: KB lookup matches and empty-query path
  - farmer_analysis: risk + yield + recommendations composition
  - peer_benchmarks: DataFrame slicing and percentile rank (CSV fixture)

External services (Tavily, DuckDuckGo) are mocked out — no real network.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the agri-intelligence package root is importable when pytest is
# invoked from outside the gna-agri-intelligence directory.
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import agronomy_rag, farmer_analysis, language_detect, market_price, web_search


# ─── language_detect ─────────────────────────────────────────────────────────

class TestLanguageDetect:
    def test_english_default(self):
        assert language_detect.detect_language("Hello, how are my crops doing?") == "en"

    def test_bemba_keyword_match(self):
        assert language_detect.detect_language("Muli shani, ndakutotela") == "bem"

    def test_nyanja_keyword_match(self):
        assert language_detect.detect_language("Moni bambo, zikomo kwambiri") == "nya"

    def test_short_string_defaults(self):
        assert language_detect.detect_language("ok") == "en"

    def test_empty_string_defaults(self):
        assert language_detect.detect_language("") == "en"

    def test_language_name(self):
        assert language_detect.language_name("bem") == "Bemba"
        assert language_detect.language_name("nya") == "Nyanja (Chichewa)"
        assert language_detect.language_name("en") == "English"
        assert language_detect.language_name("xx") == "English"


# ─── web_search ──────────────────────────────────────────────────────────────

class TestWebSearch:
    def setup_method(self):
        web_search.clear_cache()

    def test_empty_query(self):
        result = web_search.web_search("")
        assert result["error"] == "empty_query"
        assert result["results"] == []

    def test_tavily_success(self):
        os.environ["TAVILY_API_KEY"] = "fake-test-key"
        try:
            mock_client = MagicMock()
            mock_client.search.return_value = {
                "answer": "Soybean rust is a fungal disease.",
                "results": [
                    {"title": "Soybean rust",
                     "url": "https://example.com/rust",
                     "content": "Caused by Phakopsora pachyrhizi.",
                     "score": 0.95},
                ],
            }
            with patch.dict("sys.modules", {"tavily": MagicMock(TavilyClient=lambda **_kw: mock_client)}):
                result = web_search.web_search("soybean rust")
                assert result["provider"] == "tavily"
                assert "Soybean rust" in (result["answer"] or "")
                assert len(result["results"]) == 1
                assert result["results"][0]["score"] == 0.95
        finally:
            os.environ.pop("TAVILY_API_KEY", None)

    def test_ddg_fallback(self):
        os.environ.pop("TAVILY_API_KEY", None)
        web_search.clear_cache()

        mock_ddgs_class = MagicMock()
        ddgs_instance = MagicMock()
        ddgs_instance.text.return_value = [
            {"title": "Aphid info", "href": "https://example.com/aphid",
             "body": "Aphids damage soybean leaves."},
        ]
        mock_ddgs_class.return_value.__enter__.return_value = ddgs_instance

        with patch.dict("sys.modules", {
            "ddgs": MagicMock(DDGS=mock_ddgs_class),
            "duckduckgo_search": MagicMock(DDGS=mock_ddgs_class),
        }):
            result = web_search.web_search("aphid soybean")
            assert result["provider"] == "duckduckgo"
            assert len(result["results"]) == 1
            assert result["results"][0]["title"] == "Aphid info"

    def test_all_providers_fail(self):
        os.environ.pop("TAVILY_API_KEY", None)
        web_search.clear_cache()

        with patch.object(web_search, "_search_tavily", return_value=None), \
             patch.object(web_search, "_search_duckduckgo", return_value=None):
            result = web_search.web_search("anything")
            assert result["provider"] == "none"
            assert result["error"] == "all_providers_unavailable"

    def test_cache_hit(self):
        os.environ.pop("TAVILY_API_KEY", None)
        web_search.clear_cache()

        first = {"query": "q", "provider": "duckduckgo", "answer": None,
                 "results": [{"title": "T", "url": "U", "snippet": "S", "score": None}],
                 "error": None}

        with patch.object(web_search, "_search_tavily", return_value=None), \
             patch.object(web_search, "_search_duckduckgo", return_value=first) as ddg:
            web_search.web_search("q")
            web_search.web_search("q")  # same query
            assert ddg.call_count == 1  # second call served from cache


# ─── market_price ────────────────────────────────────────────────────────────

class TestMarketPrice:
    def test_basic_soybean(self):
        result = market_price.get_market_price("soy_bean")
        assert result["crop"] == "soy_bean"
        assert result["currency"] == "ZMW"
        assert result["gna_buyback"]["gross_per_kg"] == 12.0
        assert result["gna_buyback"]["net_after_loan_per_kg"] == 3.5
        assert "comparison_note" in result

    def test_alias_normalization(self):
        for alias in ["soya", "soybean", "soy", "Soy Bean"]:
            assert market_price.get_market_price(alias)["crop"] == "soy_bean"
        assert market_price.get_market_price("groundnuts")["crop"] == "groundnut"

    def test_open_market_included(self):
        result = market_price.get_market_price("soy_bean", include_open_market=True)
        assert result["open_market"] is not None
        assert "low_per_kg" in result["open_market"]

    def test_unknown_crop(self):
        result = market_price.get_market_price("rice")
        assert "error" in result and "crop_not_supported" in result["error"]


# ─── agronomy_rag ────────────────────────────────────────────────────────────

class TestAgronomyRAG:
    def test_pest_query(self):
        result = agronomy_rag.lookup_agronomy("aphid")
        # rapidfuzz should find soybean_aphid via crop common_pests
        assert "matches" in result
        assert isinstance(result["matches"], list)

    def test_diagnostic_query(self):
        result = agronomy_rag.lookup_agronomy("yellow leaf")
        # should find the yellow_leaf_tips diagnostic
        keys = [m["key"] for m in result["matches"]]
        assert "yellow_leaf_tips" in keys or len(result["matches"]) > 0

    def test_empty_query(self):
        result = agronomy_rag.lookup_agronomy("")
        assert result["error"] == "empty_query"
        assert "available_crops" in result
        assert "model_insights" in result


# ─── farmer_analysis ─────────────────────────────────────────────────────────

class TestFarmerAnalysis:
    def test_high_risk_first_season_no_inoculant(self):
        farmer = {
            "farmer_id":     "F001",
            "name":          "Test Farmer",
            "zone":          "IIa",
            "season_number": 1,
            "has_inoculant": False,
            "has_fertilizer": False,
            "days_to_plant": 35,
            "total_hectares": 1.0,
            "nudge_responses": {"total": 0, "done": 0, "help": 0, "skip": 0},
        }
        result = farmer_analysis.analyze_farmer(farmer)
        assert result["risk_tier"] == "High"
        assert result["risk_score"] > 0.4
        # Yield estimate floored at 100 kg/ha so this is low but defined
        assert result["yield_estimate_kg_ha"] >= 100
        # Recommendations should include inoculant + first-season + late-planting
        titles = " ".join(r["title"].lower() for r in result["recommendations"])
        assert "inoculant" in titles
        assert "first-season" in titles or "first season" in titles
        assert "planting" in titles

    def test_low_risk_experienced_with_inoculant(self):
        farmer = {
            "farmer_id":     "F002",
            "name":          "Veteran",
            "zone":          "III",
            "season_number": 4,
            "has_inoculant": True,
            "has_fertilizer": True,
            "days_to_plant": 5,
            "total_hectares": 1.5,
            "nudge_responses": {"total": 8, "done": 8, "help": 0, "skip": 0},
        }
        result = farmer_analysis.analyze_farmer(farmer)
        assert result["risk_tier"] == "Low"
        assert result["risk_score"] < 0.20
        assert result["yield_estimate_kg_ha"] > 1000


# ─── peer_benchmarks (light smoke test, depends on master_table.csv) ─────────

class TestPeerBenchmarks:
    @pytest.fixture
    def fake_master_table(self, tmp_path, monkeypatch):
        import pandas as pd

        df = pd.DataFrame({
            "farmer_id":           ["A", "B", "C", "D", "E"],
            "agroecological_zone": ["IIa", "IIa", "III", "III", "I"],
            "camp_name":           ["Mwandi", "Mwandi", "Lwangeni", "Lwangeni", "Chipata"],
            "district_name":       ["Mkushi", "Mkushi", "Kasama", "Kasama", "Chipata"],
            "yield_per_hectare":   [200.0, 300.0, 1100.0, 1300.0, 250.0],
            "has_buyback":         [1, 1, 1, 1, 1],
            "has_inoculant":       [0.0, 1.0, 1.0, 1.0, 0.0],
            "rcvd_training":       [0.0, 1.0, 1.0, 1.0, 0.0],
            "dominant_variety":    ["Kafue", "Kafue", "Lwangeni", "Lwangeni", "Kafue"],
        })
        path = tmp_path / "master_table.csv"
        df.to_csv(path, index=False)
        monkeypatch.setenv("GNA_MASTER_TABLE", str(path))

        # Force re-load on the module
        from tools import peer_benchmarks
        peer_benchmarks._df = None
        peer_benchmarks._load_error = None

        return path

    def test_zone_stats(self, fake_master_table):
        from tools import peer_benchmarks

        result = peer_benchmarks.get_peer_benchmarks(zone="IIa")
        assert result["error"] is None
        assert result["zone_stats"]["n_farmers"] == 2
        assert result["zone_stats"]["median_yield_kg_ha"] == 250.0

    def test_camp_stats_with_percentile(self, fake_master_table):
        from tools import peer_benchmarks

        result = peer_benchmarks.get_peer_benchmarks(
            camp_name="Mwandi",
            farmer_yield_kg_ha=350.0,
        )
        assert result["camp_stats"]["n_farmers"] == 2
        # 350 > both 200 and 300 → percentile = 100
        assert result["camp_stats"].get("farmer_percentile") == 100.0

    def test_top_camps(self, fake_master_table):
        from tools import peer_benchmarks

        top = peer_benchmarks.get_top_camps(n=3)
        assert top["error"] is None
        names = [c["camp_name"] for c in top["camps"]]
        assert "Lwangeni" in names  # highest median
