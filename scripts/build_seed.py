"""Build data/minwage.json seed from MHLW history XLSX.

Run: uv run python scripts/build_seed.py
Reads /tmp/mw_hist.xlsx (or re-downloads) and emits the bundled seed cache.
"""
from __future__ import annotations

import json
import os
import sys

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HIST_XLSX = os.environ.get("MINWAGE_HIST_XLSX", "/tmp/mw_hist.xlsx")
OUT = os.path.join(ROOT, "data", "minwage.json")


def main() -> None:
    import datetime

    from src import minwage

    wb = openpyxl.load_workbook(HIST_XLSX, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header = rows[0]
    fiscal_years: list[str] = []
    pairs: list[tuple[int, int]] = []
    c = 1
    while c < len(header):
        fy = str(header[c] or "").strip()
        if "年度" in fy:
            fiscal_years.append(fy)
            pairs.append((c, c + 1))
        c += 1

    # 47 都道府県正規化 + 加重平均
    from src import minwage
    ROMAJI = minwage.ROMAJI

    prefectures: dict[str, list[dict]] = {}
    national_weighted_average = None
    for row in rows[2:]:
        name = minwage._norm(row[0])
        if "加重平均" in name:
            for ac, _ in reversed(pairs):
                v = row[ac] if ac < len(row) else None
                if isinstance(v, (int, float)) and v:
                    national_weighted_average = int(v)
                    break
            continue
        if name not in ROMAJI:
            continue
        series = []
        for fy, (ac, dc) in zip(fiscal_years, pairs):
            amt = row[ac] if ac < len(row) else None
            dt = row[dc] if dc < len(row) else None
            amount = int(amt) if isinstance(amt, (int, float)) and amt else None
            effective_date = None
            if isinstance(dt, datetime.datetime):
                effective_date = dt.date().isoformat()
            elif isinstance(dt, str) and dt[:4].isdigit():
                effective_date = dt[:10]
            elif isinstance(dt, (int, float)):
                try:
                    effective_date = (
                        datetime.date(1899, 12, 30)
                        + datetime.timedelta(days=int(dt))
                    ).isoformat()
                except Exception:
                    pass
            series.append(
                {"fiscal_year": fy, "amount": amount, "effective_date": effective_date}
            )
        prefectures[name] = series

    dataset = {
        "fiscal_years": fiscal_years,
        "latest_fiscal_year": fiscal_years[-1],
        "national_weighted_average": national_weighted_average,
        "source_updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "prefectures_latest": {
            name: {
                "amount": s[-1]["amount"],
                "previous_amount": s[-2]["amount"] if len(s) > 1 else None,
                "effective_date": s[-1]["effective_date"],
                "fiscal_year": fiscal_years[-1],
            }
            for name, s in prefectures.items()
        },
        "history": prefectures,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    print(f"wrote {OUT}: {len(prefectures)} prefectures, "
          f"{len(fiscal_years)} fiscal years, wavg={national_weighted_average}")


if __name__ == "__main__":
    main()
