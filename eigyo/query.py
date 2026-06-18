#!/usr/bin/env python3
"""現場 → 全要員 通勤コスト一括比較 CLI（Phase 3 / Phase 4 のCLI形態）.

使い方:
    python query.py --site 東京                       # 待機中(available)の全要員を比較
    python query.py --site 新宿 --strategy fastest
    python query.py --site 東京 --status all          # 全要員（assigned 含む）
    python query.py --site 東京 --csv out.csv         # CSV 出力も
    DATA_SOURCE=ekispert_api python query.py --site 東京

要員は要員DB(SQLite)から読み、既定では status=available（待機中＝次の現場を探している人）
だけを対象にする。同じ最寄駅は 1 回しか問い合わせない（リクエスト節約）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from transit import Strategy, TransitDataError
from transit.batch import compare_site, format_table, to_csv
from transit.config import build_source, get_data_source_name, SOURCE_REGISTRY_KEY
from transit.registry import StationRegistry
from staff import SQLiteStaffRepository, StaffStatus
from staff.sqlite_repository import DEFAULT_DB_PATH

DEFAULT_STATIONS = Path(__file__).resolve().parent / "stations.json"


def run(args: argparse.Namespace) -> int:
    registry = StationRegistry.from_file(args.stations)
    repo = SQLiteStaffRepository(args.db)

    if args.status == "all":
        staffs = repo.list()
    else:
        staffs = repo.list(StaffStatus.from_str(args.status))
    if not staffs:
        print(f"対象要員がいません（status={args.status}、DB={args.db}）。"
              "`python staff_admin.py seed` でサンプル投入できます。", file=sys.stderr)
        return 1

    source = build_source(
        name=args.data_source,
        use_cache=not args.no_cache,
        request_delay_sec=args.delay,
    )

    rows = compare_site(args.site, staffs, source, registry, strategy=args.strategy)
    print(format_table(args.site, rows, args.strategy))

    if args.csv:
        Path(args.csv).write_text(to_csv(args.site, rows), encoding="utf-8-sig")
        print(f"\nCSV を書き出しました: {args.csv}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="現場→全要員 通勤コスト一括比較")
    p.add_argument("--site", required=True, help="現場（到着駅。駅名 or ID）")
    p.add_argument("--strategy", default=Strategy.CHEAPEST.value,
                   choices=[s.value for s in Strategy])
    p.add_argument("--status", default="available",
                   choices=[s.value for s in StaffStatus] + ["all"],
                   help="対象要員のステータス（既定: available）")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="要員DB(SQLite)のパス")
    p.add_argument("--stations", default=str(DEFAULT_STATIONS))
    p.add_argument("--data-source", default=None, choices=list(SOURCE_REGISTRY_KEY),
                   help=f"データソース（既定: {get_data_source_name()}）")
    p.add_argument("--csv", default=None, help="CSV 出力先パス")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--delay", type=float, default=3.0, help="リクエスト間スリープ秒")
    args = p.parse_args(argv)

    try:
        return run(args)
    except TransitDataError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
