"""Smoke tests for the MCP server (tool registration) and REST layer."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import stdio_main  # noqa: E402  (ensures import path resolves)
from src.rest import rest_app, OPENAPI_DOC  # noqa: E402

import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


def test_server_tools_registered():
    from src.server import get_server
    server = get_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert {"get_minimum_wage", "get_minimum_wage_history",
            "rank_minimum_wages", "list_prefectures"} <= names


def test_rest_latest():
    client = TestClient(rest_app())
    r = client.get("/rest/latest?prefecture=tokyo")
    assert r.status_code == 200
    body = r.json()
    assert body["amount"] == 1226
    assert body["prefecture_romaji"] == "tokyo"


def test_rest_latest_unknown():
    client = TestClient(rest_app())
    r = client.get("/rest/latest?prefecture=nowhere")
    assert r.status_code == 400


def test_rest_history():
    client = TestClient(rest_app())
    r = client.get("/rest/history?prefecture=osaka&years=3")
    assert r.status_code == 200
    assert r.json()["years_returned"] == 3


def test_rest_rank():
    client = TestClient(rest_app())
    r = client.get("/rest/rank?limit=3")
    assert r.status_code == 200
    body = r.json()
    assert len(body["cheapest"]) == 3
    assert body["national_weighted_average"] == 1121


def test_rest_prefectures():
    client = TestClient(rest_app())
    r = client.get("/rest/prefectures")
    assert r.status_code == 200
    assert len(r.json()["prefectures"]) == 47


def test_openapi_doc():
    assert OPENAPI_DOC["info"]["title"] == "Japan Minimum Wage API"
    assert "/rest/latest" in OPENAPI_DOC["paths"]
