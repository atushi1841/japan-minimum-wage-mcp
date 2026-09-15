"""RESTラッパー — japan-minimum-wage-mcp のツールを素のGETエンドポイントでも公開する。

目的: RapidAPI等のOpenAPI基盤ゲートウェイはMCP（streamable-http）を直接叩けないため、
同じ純粋Python関数（src/minwage.py）をJSONで返す /rest/* ルートが生やして必要。

設計方針（fuel-price-mcp から踏襲）:
- FastMCP http_app（Starlette）に /rest/* と /openapi.json を同じアプリに載せる。
- /mcp ルートはそのままStandby MCPとして動作し続ける。
- 認証はApifyプラットフォーム側が全ルートに実施済み。
- PPE課金はMCP経路のみ（RESTはRapidAPI側の従量課金が収益源）。
- ハンドラは同期def（minwage がブロッキングI/O）。
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from src import minwage

logger = logging.getLogger(__name__)

BASE_URL = "https://fruitful-quintessence--japan-minimum-wage-mcp.apify.actor"


def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _get_int(params: dict[str, str], key: str, default: int) -> int:
    raw = params.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"parameter '{key}' must be an integer, got {raw!r}") from None


def _pref_param(params: dict[str, str]) -> str:
    """都道府県パラメータ（prefecture 優先、region は別名）。"""
    return params.get("prefecture") or params.get("region") or "東京"


def latest_handler(request) -> JSONResponse:
    q = dict(request.query_params)
    try:
        result = minwage.latest_minimum_wage(_pref_param(q))
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("rest latest failed")
        return _err(503, f"data unavailable: {e}")
    return JSONResponse(result)


def history_handler(request) -> JSONResponse:
    q = dict(request.query_params)
    try:
        result = minwage.minimum_wage_history(_pref_param(q), _get_int(q, "years", 5))
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("rest history failed")
        return _err(503, f"data unavailable: {e}")
    return JSONResponse(result)


def rank_handler(request) -> JSONResponse:
    q = dict(request.query_params)
    try:
        result = minwage.rank_minimum_wages(_get_int(q, "limit", 10))
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("rest rank failed")
        return _err(503, f"data unavailable: {e}")
    return JSONResponse(result)


def prefectures_handler(request) -> JSONResponse:
    try:
        result = minwage.list_prefectures()
    except Exception as e:  # noqa: BLE001
        logger.exception("rest prefectures failed")
        return _err(503, f"data unavailable: {e}")
    return JSONResponse(result)


OPENAPI_DOC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {
        "title": "Japan Minimum Wage API",
        "version": "1.0.0",
        "x-category": "Data",
        "description": (
            "Official regional minimum wages for Japan (all 47 prefectures) from the "
            "Ministry of Health, Labour and Welfare (MHLW) 'Regional Minimum Wages — "
            "National Summary'. Hourly rates, year-over-year increase, and the effective "
            "date for the latest fiscal year (revised every October), plus 24 years of "
            "revision history back to FY2002. Government Standard Terms of Use 2.0."
        ),
    },
    "servers": [{"url": BASE_URL}],
    "paths": {
        "/rest/latest": {
            "get": {
                "operationId": "getMinimumWage",
                "summary": "Current minimum wage for a prefecture",
                "description": (
                    "Latest fiscal-year hourly minimum wage with previous rate, increase "
                    "amount, and effective date. Defaults to Tokyo (東京). Use prefecture="
                    "全国 for the national weighted average."
                ),
                "parameters": [
                    {"name": "prefecture", "in": "query", "required": False,
                     "description": "Kanji, romaji, or with 都/府/県 suffix (e.g. 東京, tokyo, 全国/nationwide)",
                     "schema": {"type": "string", "default": "東京"}, "example": "tokyo"},
                    {"name": "region", "in": "query", "required": False,
                     "description": "Alias of `prefecture` (prefecture wins if both sent)",
                     "schema": {"type": "string"}, "example": "tokyo"},
                ],
                "responses": {"200": {"description": "Minimum wage object"}},
            }
        },
        "/rest/history": {
            "get": {
                "operationId": "getMinimumWageHistory",
                "summary": "Revision history for a prefecture",
                "description": "Per-fiscal-year amount and effective date, plus min/max across the window.",
                "parameters": [
                    {"name": "prefecture", "in": "query", "required": False,
                     "description": "Region name (kanji/romaji) or 全国/nationwide",
                     "schema": {"type": "string", "default": "東京"}, "example": "osaka"},
                    {"name": "region", "in": "query", "required": False,
                     "description": "Alias of `prefecture`",
                     "schema": {"type": "string"}, "example": "osaka"},
                    {"name": "years", "in": "query", "required": False,
                     "description": "Recent fiscal years (1-24)",
                     "schema": {"type": "integer", "default": 5, "minimum": 1, "maximum": 24}},
                ],
                "responses": {"200": {"description": "History object with series"}},
            }
        },
        "/rest/rank": {
            "get": {
                "operationId": "rankMinimumWages",
                "summary": "Cheapest / most expensive prefecture ranking",
                "description": "47 prefectures ranked by current hourly rate (cheapest first, plus most expensive end).",
                "parameters": [
                    {"name": "limit", "in": "query", "required": False,
                     "description": "Entries per end of the ranking (1-47)",
                     "schema": {"type": "integer", "default": 10, "minimum": 1, "maximum": 47}},
                ],
                "responses": {"200": {"description": "Ranking object"}},
            }
        },
        "/rest/prefectures": {
            "get": {
                "operationId": "listPrefectures",
                "summary": "List queryable prefectures and fiscal years (self-discovery)",
                "description": "47 prefectures with romaji aliases; fiscal years available; latest fiscal year; national weighted average.",
                "parameters": [],
                "responses": {"200": {"description": "Prefectures/years catalogue"}},
            }
        },
    },
}


def openapi_handler(request) -> JSONResponse:
    return JSONResponse(OPENAPI_DOC)


def rest_routes() -> list[BaseRoute]:
    return [
        Route("/openapi.json", openapi_handler, methods=["GET"]),
        Route("/rest/latest", latest_handler, methods=["GET"]),
        Route("/rest/history", history_handler, methods=["GET"]),
        Route("/rest/rank", rank_handler, methods=["GET"]),
        Route("/rest/prefectures", prefectures_handler, methods=["GET"]),
    ]


def rest_app() -> Starlette:
    return Starlette(routes=rest_routes())
