# Japan Minimum Wage MCP — 日本の地域別最低賃金（厚生労働省 公式統計）

[![MCP](https://img.shields.io/badge/MCP-server-blue)](https://smithery.ai)
[![License: Gov Terms 2.0](https://img.shields.io/badge/license-政府標準利用規約2.0-green)](https://www.gov-online.go.jp/usagi/plan2.html)

MHLW（厚生労働省）公式統計「地域別最低賃金の全国一覧」から、日本の **47 都道府県の地域別最低賃金** を
AI エージェント（Claude, ChatGPT 等）から MCP ツールとして照会できるサーバーです。

LLM の記憶は設計上すぐに古くなります。最低賃金は毎年 10 月に改定されるため、
「現在の正しい最低賃金」を答えるには常に最新の公式ソースが必要です。
HR SaaS・給与計算・コンプライアンス・コスト見積もりの自動化に利用できます。

## 提供ツール

| ツール | 内容 |
|---|---|
| `get_minimum_wage` | 指定都道府県の最新年度最低賃金時間額＋前年度額＋引上げ額＋発効日（全国加重平均も可） |
| `get_minimum_wage_history` | 指定都道府県の過去 N 年度の改定履歴（金額＋発効日、min/max 付き） |
| `rank_minimum_wages` | 47 都道府県の時間額ランキング（安い順＋高い順、全国加重平均付き） |
| `list_prefectures` | 照会可能な都道府県名（漢字・ローマ字）・利用可能年度・最新年度の一覧 |

## データ

- **ソース**: 厚生労働省「地域別最低賃金の全国一覧」
  （`https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/koyou_roudou/roudoukijun/minimumichiran/`）
- **最新年度**: 令和 7 年度（2025 年 10 月〜 2026 年 3 月にかけて順次発効）
- **履歴**: 平成 14 年度（2002）〜 令和 7 年度まで 24 年度分
- **全国加重平均**: 1,121 円/時間（令和 7 年度）
- **単位**: 円/時間（JPY per hour）
- **ライセンス**: 政府標準利用規約 2.0（出典明示で商用利用可）

## クイックスタート

```bash
uv run src/stdio_main.py          # stdio MCP サーバー（Claude Desktop / Cursor / Smithery）
```

Claude Desktop の `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "japan-minimum-wage": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/japan-minimum-wage-mcp", "src/stdio_main.py"]
    }
  }
}
```

## REST（RapidAPI / OpenAPI ゲートウェイ用）

Apify Standby 起動時（`uv run -m src.main`）は `/mcp`（streamable-http）に加え、
素の GET エンドポイントも公開します（RapidAPI freemium 公開用）:

| エンドポイント | 説明 |
|---|---|
| `GET /rest/latest?prefecture=tokyo` | 最新最低賃金 |
| `GET /rest/history?prefecture=osaka&years=5` | 改定履歴 |
| `GET /rest/rank?limit=10` | 安い順ランキング |
| `GET /rest/prefectures` | 都道府県・年度一覧 |
| `GET /openapi.json` | OpenAPI ドキュメント |

## 開発

```bash
uv venv && uv pip install -e .
uv pip install pytest
python scripts/build_seed.py     # data/minwage.json を MHLW XLSX から再生成
pytest -q                       # 18 tests
python scripts/sync_manifest_tools.py   # manifest.json の tools をサーバーから同期
python scripts/pack_mcpb.py             # server.mcpb（Smithery 公開用）を生成
```

## 仕様

- 完全ローカル実行（API キー不要）。同梱シードキャッシュ（`data/minwage.json`）から即応答
- `force=True` 指定時のみ MHLW 公式 XLSX を再取得（年 1 回更新のため TTL 24h）
- 読み取り専用・閉世界（ツールは失敗しない）設計

---

出典: 厚生労働省「地域別最低賃金の全国一覧」/ Government Standard Terms of Use 2.0.
