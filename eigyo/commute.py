#!/usr/bin/env python3
"""通勤コスト調査 原型 CLI.

使い方:
    python commute.py 平井 東京
    python commute.py 2927 2590 --strategy fastest
    python commute.py sf-2927 st-2590 --json
    python commute.py 平井 東京 --no-cache             # キャッシュを無視して取り直す
    DATA_SOURCE=ekispert_api python commute.py 平井 東京  # データソース切替（環境変数）

この上位層は TransitDataSource と CommuteResult にしか依存しない。
どのデータソース（ekitan_scraper / ekispert_api）を使うかは config.build_source が
環境変数 DATA_SOURCE で決める。差し替えても、ここは 1 行も変えなくてよい設計。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from transit import CommuteResult, Strategy, TransitDataError
from transit.cache import DEFAULT_TTL_SEC
from transit.config import build_source, get_data_source_name, SOURCE_REGISTRY_KEY
from transit.registry import StationRegistry

DEFAULT_STATIONS = Path(__file__).resolve().parent / "stations.json"


def run(args: argparse.Namespace) -> int:
    registry = StationRegistry.from_file(args.stations)

    source = build_source(
        name=args.data_source,
        use_cache=not args.no_cache,
        ttl_sec=int(args.ttl_days * 86400) if args.ttl_days >= 0 else -1,
        request_delay_sec=args.delay,
    )
    # アクティブなソースの体系で駅IDに解決（差し替え契約：業務層はソース名だけ意識）
    reg_key = SOURCE_REGISTRY_KEY.get(source.name, "ekitan")
    from_id = registry.resolve(args.from_station, reg_key)
    to_id = registry.resolve(args.to_station, reg_key)

    result: CommuteResult = source.query(
        from_id, to_id, strategy=Strategy.from_str(args.strategy)
    )

    if args.json:
        print(result.to_json(indent=2))
    else:
        print(result.one_line())
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows コンソール(GBK)で日本語を出すと化ける/落ちるため UTF-8 に固定
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(
        description="ekitan から通勤時間・乗換・運賃・通勤定期代を取得する原型ツール"
    )
    p.add_argument("from_station", help="出発駅（駅名 または ID: 2927 / sf-2927）")
    p.add_argument("to_station", help="到着駅（駅名 または ID: 2590 / st-2590）")
    p.add_argument(
        "--strategy",
        default=Strategy.CHEAPEST.value,
        choices=[s.value for s in Strategy],
        help="経路選択方針（既定: cheapest=定期代最安、同額なら時間最短）",
    )
    p.add_argument(
        "--stations",
        default=str(DEFAULT_STATIONS),
        help=f"駅マスタ JSON のパス（既定: {DEFAULT_STATIONS.name}）",
    )
    p.add_argument(
        "--data-source",
        default=None,
        choices=list(SOURCE_REGISTRY_KEY),
        help=f"データソース（既定: 環境変数 DATA_SOURCE か {get_data_source_name()}）",
    )
    p.add_argument("--json", action="store_true", help="結果を JSON で出力")
    p.add_argument(
        "--no-cache", action="store_true", help="キャッシュを無視して取り直す"
    )
    p.add_argument(
        "--ttl-days",
        type=float,
        default=DEFAULT_TTL_SEC / 86400,
        help="キャッシュ有効日数（既定: 30。負値で無期限）",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="リクエスト間スリープ秒（既定: 3.0。責任あるクロールのため 2 以上推奨）",
    )
    args = p.parse_args(argv)

    try:
        return run(args)
    except TransitDataError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
