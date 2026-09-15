"""MHLW 地域別最低賃金 MCP サーバー。

厚生労働省「地域別最低賃金の全国一覧」の47都道府県データ（時間額・引上げ額・
発効日・過去24年度の改定履歴）を、AIエージェント（Claude, ChatGPT等）から
MCPツールとして呼び出せます。

get_server() がコントラクト関数。main.py がこれを呼び出し、uvicorn 経由で
Apify Standby モードでホスティングします。データは初回呼び出し時にシード
キャッシュを読み込み、force 指定時のみ MHLW 公式XLSX を再取得します。

課金は pay_per_event.json に定義された PPE イベント経由で行われます。
"""

from __future__ import annotations

import os

from fastmcp import FastMCP

from src import minwage

if os.environ.get("APIFY_CONTAINER_PORT"):
    from apify import Actor
else:
    from src.apify_shim import Actor  # type: ignore[assignment]

_READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

_ATTRIBUTION = minwage.ATTRIBUTION


def get_server() -> FastMCP:
    try:
        server = FastMCP("japan-minimum-wage-mcp", version="0.1.0")
    except TypeError:
        server = FastMCP("japan-minimum-wage-mcp")

    @server.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def get_minimum_wage(prefecture: str = "東京") -> dict:
        """Get the current regional minimum wage (hourly rate) for a Japanese prefecture.

        Official MHLW (Ministry of Health, Labour and Welfare) regional minimum wage,
        revised every October. Returns the latest fiscal-year hourly rate, previous rate,
        the amount of increase, and the effective date. LLM memory is stale by design —
        this tool always returns the current official figures.

        Args:
            prefecture: prefecture name in kanji, romaji, or with 都/府/県 suffix
                        (e.g. 東京, tokyo, 東京都). Use 全国/nationwide for the
                        national weighted average.
        """
        await Actor.charge("minimum-wage-latest")
        result = minwage.latest_minimum_wage(prefecture)
        return {"type": "text", "text": _format_latest(result), "structuredContent": result}

    @server.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def get_minimum_wage_history(prefecture: str = "東京", years: int = 5) -> dict:
        """Get the revision history of a prefecture's minimum wage over recent fiscal years.

        Official MHLW data back to FY2002 (平成14年度). Returns per-fiscal-year
        amount and effective date, plus min/max across the window.

        Args:
            prefecture: prefecture name (kanji or romaji)
            years: number of recent fiscal years to return (default 5, max 24)
        """
        await Actor.charge("minimum-wage-history")
        result = minwage.minimum_wage_history(prefecture, years)
        return {"type": "text", "text": _format_history(result), "structuredContent": result}

    @server.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def rank_minimum_wages(limit: int = 10) -> dict:
        """Rank Japan's 47 prefectures by current minimum wage (cheapest first, plus most expensive).

        Useful for cost-of-labor comparisons, relocation planning, and HR budgeting.
        National weighted average is included for reference.

        Args:
            limit: entries per end of the ranking (default 10, max 47)
        """
        await Actor.charge("minimum-wage-rank")
        result = minwage.rank_minimum_wages(limit)
        return {"type": "text", "text": _format_rank(result), "structuredContent": result}

    @server.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def list_prefectures() -> dict:
        """List all 47 prefectures with romaji aliases and the available fiscal years.

        Call this first if unsure which prefecture names are valid, or to discover
        the historical coverage (平成14年度 through 令和７年度).

        Returns:
            Prefectures with kanji + romaji names, fiscal years available,
            latest fiscal year, and the national weighted average.
        """
        await Actor.charge("minimum-wage-latest")
        result = minwage.list_prefectures()
        lines = [
            f"Latest fiscal year: {result['latest_fiscal_year']}",
            f"National weighted average: {result['national_weighted_average']} JPY/hour",
            f"Fiscal years available: {', '.join(result['fiscal_years_available'])}",
            "",
            "Prefectures (name / romaji):",
            "  " + ", ".join(f"{p['name']}={p['romaji']}" for p in result["prefectures"]),
            "",
            _ATTRIBUTION,
        ]
        return {"type": "text", "text": "\n".join(lines), "structuredContent": result}

    return server


def _format_latest(r: dict) -> str:
    eff = r.get("effective_date") or "(nationwide average)"
    inc = r.get("increase")
    inc_s = f"{inc:+d}" if isinstance(inc, int) else "n/a"
    lines = [
        f"{r['prefecture']} ({r['prefecture_romaji']}) — regional minimum wage",
        f"  {r['amount']} JPY/hour ({r['fiscal_year']}, effective {eff})",
        f"  previous: {r['previous_amount']} JPY/hour, increase: {inc_s}",
        "",
        _ATTRIBUTION,
    ]
    return "\n".join(lines)


def _format_history(r: dict) -> str:
    amounts = [s["amount"] for s in r["series"] if s["amount"] is not None]
    lines = [
        f"{r['prefecture']} — minimum wage history ({r['years_returned']} fiscal years, JPY/hour)",
    ]
    if amounts:
        lines.append(f"  min {min(amounts)} / max {max(amounts)}")
    for s in r["series"][-8:]:
        lines.append(f"  {s['fiscal_year']}: {s['amount']} (effective {s['effective_date']})")
    if r["years_returned"] > 8:
        lines.append(f"  ... ({r['years_returned'] - 8} earlier years in structuredContent)")
    lines.append("")
    lines.append(_ATTRIBUTION)
    return "\n".join(lines)


def _format_rank(r: dict) -> str:
    lines = [
        f"Minimum wage ranking — {r['fiscal_year']} (JPY/hour)",
        f"  national weighted average: {r['national_weighted_average']}",
    ]
    for i, row in enumerate(r["cheapest"], 1):
        lines.append(f"  {i}. {row['prefecture']} ({row['romaji']}): {row['amount']}")
    lines.append("")
    lines.append("Most expensive:")
    for i, row in enumerate(r["most_expensive"], 1):
        lines.append(f"  {i}. {row['prefecture']} ({row['romaji']}): {row['amount']}")
    lines.append("")
    lines.append(_ATTRIBUTION)
    return "\n".join(lines)
