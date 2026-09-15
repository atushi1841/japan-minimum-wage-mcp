"""MHLW 地域別最低賃金データコア — 厚生労働省「地域別最低賃金の全国一覧」パーサー。

ソース: https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/koyou_roudou/roudoukijun/minimumichiran/
  - 001571192.pdf = 令和7年度地域別最低賃金全国一覧（47都道府県 時間額 + 引上げ額 + 発効日）
  - 001571219.xlsx = 平成14年度〜令和7年度 地域別最低賃金改定状況（47都道府県 × 24年度の
    改定額(円) と 発効年月日 の履歴）

利用規約: 政府標準利用規約2.0（出典明示で商用可）— METI 燃料価格データと同等のライセンス姿勢。

このモジュールは:
1. 001571219.xlsx を openpyxl(read_only) でパースし、都道府県別・年度別の
   改定額 + 発効日のリストを構築
2. 001571192.pdf（または同梱シード）の R7 最新値を優先して latest を決定
3. JSON キャッシュ（MINWAGE_DATA_DIR、コンテナ内は /tmp）に保存、以後はキャッシュ優先
4. データ未保持時は MHLW 公式XLSX を遅延取得（ブラウザUA必須・フォールバック付き）
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import tempfile
import urllib.request
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# 令和7年度全国一覧PDF（47都道府県 時間額+引上げ額+発効日）
MHLW_LIST_PDF = "https://www.mhlw.go.jp/content/11200000/001571192.pdf"
# 平成14年度〜令和7年度 改定状況XLSX
MHLW_HISTORY_XLSX = "https://www.mhlw.go.jp/content/11200000/001571219.xlsx"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

CACHE_TTL_SECONDS = 24 * 3600  # 1日1回再チェック（年1回更新のため長め）
FETCH_FAIL_BACKOFF = 3600

# ビルド時に同梱されるシードキャッシュ
SEED_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "minwage.json"
)

# 47都道府県（JIS X 0401 順）＋ ローマ字エイリアス
PREFECTURES: list[str] = [
    "北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島",
    "茨城", "栃木", "群馬", "埼玉", "千葉", "東京", "神奈川",
    "新潟", "富山", "石川", "福井", "山梨", "長野",
    "岐阜", "静岡", "愛知", "三重",
    "滋賀", "京都", "大阪", "兵庫", "奈良", "和歌山",
    "鳥取", "島根", "岡山", "広島", "山口",
    "徳島", "香川", "愛媛", "高知",
    "福岡", "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島", "沖縄",
]

ROMAJI: dict[str, str] = {
    "北海道": "hokkaido", "青森": "aomori", "岩手": "iwate", "宮城": "miyagi",
    "秋田": "akita", "山形": "yamagata", "福島": "fukushima", "茨城": "ibaraki",
    "栃木": "tochigi", "群馬": "gunma", "埼玉": "saitama", "千葉": "chiba",
    "東京": "tokyo", "神奈川": "kanagawa", "新潟": "niigata", "富山": "toyama",
    "石川": "ishikawa", "福井": "fukui", "山梨": "yamanashi", "長野": "nagano",
    "岐阜": "gifu", "静岡": "shizuoka", "愛知": "aichi", "三重": "mie",
    "滋賀": "shiga", "京都": "kyoto", "大阪": "osaka", "兵庫": "hyogo",
    "奈良": "nara", "和歌山": "wakayama", "鳥取": "tottori", "島根": "shimane",
    "岡山": "okayama", "広島": "hiroshima", "山口": "yamaguchi", "徳島": "tokushima",
    "香川": "kagawa", "愛媛": "ehime", "高知": "kochi", "福岡": "fukuoka",
    "佐賀": "saga", "長崎": "nagasaki", "熊本": "kumamoto", "大分": "oita",
    "宮崎": "miyazaki", "鹿児島": "kagoshima", "沖縄": "okinawa",
}
ROMAJI_TO_JA: dict[str, str] = {v: k for k, v in ROMAJI.items()}

ATTRIBUTION = (
    "Source: Ministry of Health, Labour and Welfare (MHLW) Japan, "
    "'Regional Minimum Wages — National Summary' (地域別最低賃金の全国一覧). "
    "Government Standard Terms of Use 2.0."
)


# ──────────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────────

def _data_dir() -> str:
    d = os.environ.get("MINWAGE_DATA_DIR") or os.path.join(
        os.environ.get("APIFY_ACTOR_STORAGE_DIR", tempfile.gettempdir()), "minwage-cache"
    )
    os.makedirs(d, exist_ok=True)
    return d


def cache_path() -> str:
    return os.path.join(_data_dir(), "minwage.json")


def _norm(s: Any) -> str:
    return re.sub(r"[\s　]+", "", str(s)) if s is not None else ""


def normalize_prefecture(name: str) -> str:
    """入力都道府県名を正規化（空白・都/府/県接尾辞・ローマ字対応）。"""
    s = _norm(name).strip()
    if not s:
        return "全国"
    key = s.lower()
    if key in ROMAJI_TO_JA:
        return ROMAJI_TO_JA[key]
    stripped = re.sub(r"(都|府|県)$", "", s)
    if stripped in ROMAJI:
        return stripped
    if s in ROMAJI:
        return s
    return s


def _load_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _http_get(url: str, timeout: int = 90) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:  # noqa: BLE001
        logger.info(f"GET {url} failed: {e}")
        return None


# ──────────────────────────────────────────────
# XLSX パース（改定状況 平成14〜令和7）
# ──────────────────────────────────────────────

def parse_history_workbook(xlsx_bytes: bytes) -> dict[str, Any]:
    """001571219.xlsx をパース。

    返り値: {
      "fiscal_years": [ "平成14年度", ... , "令和７年度" ],
      "prefectures": { "<都道府県名>": [
          {"fiscal_year": str, "amount": int, "effective_date": "YYYY-MM-DD"|None}, ...
      ], ...},
    }
    XLSX 構造: 行0=年度ヘッダ(2列おき: 改定額,発効日)、行1=列ラベル、
    行2以降=都道府県ごとの改定額/発効日。
    """
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header = rows[0]
    # 年度はインデックス 1 から 2 刻み（[改定額, 発効日] のペア）
    fiscal_years: list[str] = []
    pairs: list[tuple[int, int]] = []  # (amount_col, date_col)
    c = 1
    while c < len(header):
        fy = _norm(header[c])
        if fy and ("年度" in fy or "年度" in (str(header[c]) or "")):
            fiscal_years.append(str(header[c]).strip())
            pairs.append((c, c + 1))
        c += 1

    prefectures: dict[str, list[dict[str, Any]]] = {}
    national_weighted_average: int | None = None
    for row in rows[2:]:
        name = _norm(row[0])
        if "加重平均" in name and national_weighted_average is None:
            # 全国加重平均額の最新年度額を記録
            last_amt = None
            for ac, _dc in reversed(pairs):
                v = row[ac] if ac < len(row) else None
                if isinstance(v, (int, float)) and v:
                    last_amt = int(v)
                    break
            national_weighted_average = last_amt
            continue
        # その他の集計行をスキップ
        if name not in ROMAJI:
            continue
        series: list[dict[str, Any]] = []
        for fy, (ac, dc) in zip(fiscal_years, pairs):
            amt = row[ac] if ac < len(row) else None
            dt = row[dc] if dc < len(row) else None
            amount = int(amt) if isinstance(amt, (int, float)) and amt else None
            effective_date = None
            if isinstance(dt, datetime):
                effective_date = dt.date().isoformat()
            elif isinstance(dt, str) and re.match(r"\d{4}-\d{2}-\d{2}", dt):
                effective_date = dt[:10]
            elif isinstance(dt, (int, float)):
                # Excel serial date
                try:
                    effective_date = (
                        datetime(1899, 12, 30) + __import__("datetime").timedelta(days=int(dt))
                    ).date().isoformat()
                except Exception:  # noqa: BLE001
                    effective_date = None
            series.append({
                "fiscal_year": fy,
                "amount": amount,
                "effective_date": effective_date,
            })
        prefectures[name] = series
    return {
        "fiscal_years": fiscal_years,
        "national_weighted_average": national_weighted_average,
        "prefectures": prefectures,
    }


# ──────────────────────────────────────────────
# PDF パース（R7 最新一覧）— 軽量フォールバック
# ──────────────────────────────────────────────
# PDF 抽出は環境に依存するため、シード優先。PDF があれば行ごとに
# 「都道府県 時間額(改定前) 引上げ額 引上げ率 発効日」を正規表現で拾う。

def parse_list_pdf(pdf_bytes: bytes) -> dict[str, dict[str, Any]] | None:
    """001571192.pdf から R7 最新一覧を抽出（失敗時は None）。"""
    try:
        import pdfplumber  # type: ignore
    except Exception:  # noqa: BLE001
        logger.info("pdfplumber not available; skipping PDF parse")
        return None
    try:
        text = ""
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
    except Exception as e:  # noqa: BLE001
        logger.info(f"PDF parse failed: {e}")
        return None

    out: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        m = re.match(
            r"([一-龯ぁ-ん　 ]+?)\s*([0-9,]+)\s*[（(]\s*([0-9,]+)\s*[）)]\s*([0-9]+)\s*([0-9.]+)\s*(令和[0-9]+年[0-9]+月[0-9]+日)",
            line,
        )
        if not m:
            continue
        name = re.sub(r"[　 ]", "", m.group(1))
        if name not in ROMAJI:
            continue
        out[name] = {
            "amount": int(m.group(2).replace(",", "")),
            "previous_amount": int(m.group(3).replace(",", "")),
            "increase": int(m.group(4)),
            "increase_rate": float(m.group(5)),
            "effective_jp": m.group(6),
        }
    return out if out else None


# ──────────────────────────────────────────────
# 取得・キャッシュ・ビルド
# ──────────────────────────────────────────────

def build_dataset(force: bool = False) -> dict[str, Any]:
    """シード/キャッシュから構築。force 時は MHLW から取得を試みる。"""
    cached = _load_json(cache_path()) if not force else None
    if cached and not force:
        return cached

    parsed: dict[str, Any] | None = None
    if force:
        xlsx = _http_get(MHLW_HISTORY_XLSX)
        if xlsx and xlsx[:2] == b"PK":
            try:
                parsed = parse_history_workbook(xlsx)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"history xlsx parse failed: {e}")
        # R7 最新一覧はシードの latest を信頼（PDF は任意）
        pdf = _http_get(MHLW_LIST_PDF)
        if pdf and pdf[:4] == b"%PDF":
            r7 = parse_list_pdf(pdf)
            if r7 and parsed:
                for name, info in r7.items():
                    if name in parsed["prefectures"]:
                        parsed["prefectures"][name][-1]["amount"] = info["amount"]
                        if info.get("effective_jp"):
                            parsed["prefectures"][name][-1]["effective_jp"] = info["effective_jp"]

    if parsed:
        dataset = _finalize(parsed)
        _save(dataset)
        return dataset

    # 取得失敗 → シード
    seed = _load_json(SEED_PATH)
    if seed:
        logger.warning("serving seed minimum-wage cache")
        return seed
    raise RuntimeError("MHLW minimum wage XLSX could not be fetched and no seed exists")


def _finalize(parsed: dict[str, Any]) -> dict[str, Any]:
    """履歴から latest（最新年度）を抽出して一体化。"""
    fy = parsed["fiscal_years"]
    latest_label = fy[-1] if fy else "令和７年度"
    latest: dict[str, dict[str, Any]] = {}
    for name, series in parsed["prefectures"].items():
        last = series[-1] if series else None
        prev = series[-2] if len(series) > 1 else None
        latest[name] = {
            "amount": last["amount"] if last else None,
            "previous_amount": prev["amount"] if prev else None,
            "effective_date": last["effective_date"] if last else None,
            "fiscal_year": latest_label,
        }
    return {
        "fiscal_years": fy,
        "latest_fiscal_year": latest_label,
        "national_weighted_average": parsed.get("national_weighted_average"),
        "source_updated": datetime.now().isoformat(timespec="seconds"),
        "prefectures_latest": latest,
        "history": parsed["prefectures"],
    }


def _save(dataset: dict[str, Any]) -> None:
    path = cache_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False)
    os.replace(tmp, path)


# ──────────────────────────────────────────────
# クエリAPI（server.py / rest.py から呼ばれる）
# ──────────────────────────────────────────────

def get_dataset() -> dict[str, Any]:
    return build_dataset()


def latest_minimum_wage(prefecture: str = "全国") -> dict[str, Any]:
    data = get_dataset()
    if prefecture in ("全国", "nationwide"):
        return {
            "prefecture": "全国",
            "prefecture_romaji": "nationwide",
            "amount": data.get("national_weighted_average"),
            "currency": "JPY",
            "unit": "per hour",
            "fiscal_year": data.get("latest_fiscal_year"),
            "effective_date": None,
            "source": ATTRIBUTION,
        }
    name = normalize_prefecture(prefecture)
    latest = data["prefectures_latest"].get(name)
    if not latest:
        known = sorted(data["prefectures_latest"].keys())
        raise ValueError(f"unknown prefecture '{name}'. Known: {', '.join(known)}")
    return {
        "prefecture": name,
        "prefecture_romaji": ROMAJI.get(name, name),
        "amount": latest["amount"],
        "previous_amount": latest["previous_amount"],
        "increase": (
            (latest["amount"] - latest["previous_amount"])
            if latest["amount"] is not None and latest["previous_amount"] is not None
            else None
        ),
        "currency": "JPY",
        "unit": "per hour",
        "fiscal_year": latest["fiscal_year"],
        "effective_date": latest["effective_date"],
        "source": ATTRIBUTION,
    }


def minimum_wage_history(prefecture: str, years: int = 5) -> dict[str, Any]:
    data = get_dataset()
    name = normalize_prefecture(prefecture)
    series = data.get("history", {}).get(name)
    if not series:
        raise ValueError(f"unknown prefecture '{name}'")
    years = max(1, min(int(years), len(series)))
    window = series[-years:]
    amounts = [s["amount"] for s in window if s["amount"] is not None]
    return {
        "prefecture": name,
        "prefecture_romaji": ROMAJI.get(name, name),
        "currency": "JPY",
        "unit": "per hour",
        "years_returned": len(window),
        "series": window,
        "min": min(amounts) if amounts else None,
        "max": max(amounts) if amounts else None,
        "source": ATTRIBUTION,
    }


def rank_minimum_wages(limit: int = 10) -> dict[str, Any]:
    """47都道府県を時間額で安い順ランキング。"""
    data = get_dataset()
    rows = [
        {
            "prefecture": name,
            "romaji": ROMAJI.get(name, name),
            "amount": info["amount"],
            "effective_date": info["effective_date"],
        }
        for name, info in data["prefectures_latest"].items()
        if info["amount"] is not None
    ]
    rows.sort(key=lambda r: r["amount"])
    limit = max(1, min(int(limit), 47))
    return {
        "fiscal_year": data.get("latest_fiscal_year"),
        "currency": "JPY",
        "unit": "per hour",
        "national_weighted_average": data.get("national_weighted_average"),
        "cheapest": rows[:limit],
        "most_expensive": list(reversed(rows[-limit:])),
        "source": ATTRIBUTION,
    }


def list_prefectures() -> dict[str, Any]:
    data = get_dataset()
    return {
        "prefectures": [{"name": p, "romaji": ROMAJI.get(p, p)} for p in PREFECTURES],
        "fiscal_years_available": data.get("fiscal_years", []),
        "latest_fiscal_year": data.get("latest_fiscal_year"),
        "national_weighted_average": data.get("national_weighted_average"),
        "source": ATTRIBUTION,
    }
