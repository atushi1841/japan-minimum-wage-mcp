"""Tests for japan-minimum-wage-mcp data core and API layer."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import minwage  # noqa: E402


def test_seed_present_and_valid():
    data = minwage._load_json(minwage.SEED_PATH)
    assert data is not None, "seed cache missing"
    assert len(data["prefectures_latest"]) == 47, "expected 47 prefectures"
    assert len(data["fiscal_years"]) == 24
    assert data["national_weighted_average"] == 1121


def test_latest_tokyo():
    r = minwage.latest_minimum_wage("東京")
    assert r["amount"] == 1226
    assert r["previous_amount"] == 1163
    assert r["increase"] == 63
    assert r["effective_date"] == "2025-10-03"
    assert r["fiscal_year"] == "令和７年度"


def test_latest_romaji_and_suffix():
    assert minwage.latest_minimum_wage("tokyo")["amount"] == 1226
    assert minwage.latest_minimum_wage("東京都")["amount"] == 1226
    assert minwage.latest_minimum_wage("TOKYO")["amount"] == 1226


def test_nationwide_weighted_average():
    r = minwage.latest_minimum_wage("全国")
    assert r["amount"] == 1121
    r2 = minwage.latest_minimum_wage("nationwide")
    assert r2["amount"] == 1121


def test_unknown_prefecture_raises():
    with pytest.raises(ValueError):
        minwage.latest_minimum_wage("存在しない県")


def test_history_osaka():
    r = minwage.minimum_wage_history("大阪", 3)
    assert r["years_returned"] == 3
    assert r["series"][-1]["amount"] == 1177
    assert r["series"][-1]["fiscal_year"] == "令和７年度"
    assert r["max"] == 1177


def test_history_clamped_to_range():
    r = minwage.minimum_wage_history("東京", 999)
    assert r["years_returned"] == 24


def test_rank():
    r = minwage.rank_minimum_wages(5)
    assert r["national_weighted_average"] == 1121
    assert len(r["cheapest"]) == 5
    assert len(r["most_expensive"]) == 5
    # cheapest first
    assert r["cheapest"][0]["amount"] <= r["cheapest"][1]["amount"]
    # top should be Tokyo/Osaka/Kanagawa class
    top = {p["prefecture"] for p in r["most_expensive"]}
    assert "東京" in top


def test_list_prefectures():
    r = minwage.list_prefectures()
    assert len(r["prefectures"]) == 47
    names = {p["name"] for p in r["prefectures"]}
    assert "北海道" in names and "沖縄" in names
    assert r["latest_fiscal_year"] == "令和７年度"


def test_normalize_prefecture():
    assert minwage.normalize_prefecture("Osaka") == "大阪"
    assert minwage.normalize_prefecture(" 東 京 ") == "東京"
    assert minwage.normalize_prefecture("") == "全国"


def test_dataset_build_from_seed():
    # build_dataset without force should load the seed (cache may be empty)
    d = minwage.build_dataset()
    assert len(d["prefectures_latest"]) == 47
