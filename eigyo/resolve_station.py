#!/usr/bin/env python3
"""駅名から ekitan 駅IDを引く補助ツール（stations.json 整備用）.

ekitan の駅サジェスト API を使い、駅名 → 駅ID（code）を表示する。
誤ID登録の事故（例: 池袋に塔ノ沢のIDを入れる）を防ぐための確認用。

使い方:
    python resolve_station.py 池袋 横浜 川口          # 候補を表示（首都圏を優先）
    python resolve_station.py 平井 --all              # 全国の同名候補も表示
    python resolve_station.py 池袋 横浜 --add          # 首都圏で一意なら stations.json に追記

責任ある利用のため、駅名ごとに 1 リクエスト・直列・低頻度で叩く。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

from transit.station_lookup import suggest, AREA_LABELS, AREA_TOKYO

DEFAULT_STATIONS = Path(__file__).resolve().parent / "stations.json"


def fmt(s: dict) -> str:
    area = s.get("area", "")
    label = AREA_LABELS.get(area, f"area{area}")
    return (
        f"  code={s.get('code'):>5}  {s.get('name')}"
        f"  （{s.get('ruby')} / {label} / 路線:{s.get('company')}）"
    )


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="駅名→ekitan駅ID 検索（stations.json 整備用）")
    p.add_argument("names", nargs="+", help="調べたい駅名（複数可）")
    p.add_argument("--all", action="store_true", help="首都圏(area=0)以外も表示")
    p.add_argument(
        "--add",
        action="store_true",
        help="首都圏で名前が一意に決まる駅を stations.json に追記",
    )
    p.add_argument("--stations", default=str(DEFAULT_STATIONS))
    p.add_argument("--delay", type=float, default=2.0, help="リクエスト間スリープ秒")
    args = p.parse_args(argv)

    session = requests.Session()
    to_add: dict[str, str] = {}

    for i, name in enumerate(args.names):
        if i > 0:
            time.sleep(args.delay)  # 直列・低頻度
        try:
            results = suggest(name, session)
        except requests.RequestException as e:
            print(f"[{name}] 取得失敗: {e}", file=sys.stderr)
            continue

        shown = results if args.all else [r for r in results if r.get("area") == AREA_TOKYO]
        print(f"=== {name} : {len(shown)}件" + ("（首都圏のみ）" if not args.all else "（全国）") + " ===")
        if not shown:
            print("  該当なし（--all で全国を確認 / 表記ゆれを確認）")
            continue
        for s in shown:
            print(fmt(s))

        # 首都圏で name 完全一致が 1 件だけなら追記候補に
        exact = [r for r in results if r.get("area") == AREA_TOKYO and r.get("name") == name]
        if args.add:
            if len(exact) == 1:
                to_add[name] = exact[0]["code"]
            else:
                print(
                    f"  → 追記スキップ（首都圏の完全一致が {len(exact)} 件で曖昧）。"
                    "正しい code を手で stations.json へ。",
                    file=sys.stderr,
                )

    if args.add and to_add:
        path = Path(args.stations)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data.update(to_add)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nstations.json に追記: {to_add}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
