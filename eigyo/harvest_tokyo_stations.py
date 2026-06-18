#!/usr/bin/env python3
"""首都圏（area=0）の全駅IDを ekitan の時刻表ページから一括収集する.

経路（responsible: 公開ページのみ・直列・低頻度・robots 準拠）:
  1. /timetable/railway/area/0          … 首都圏の路線一覧（約160路線）
  2. /timetable/railway/line/<lineId>   … 各路線の全駅（/station/<駅ID> リンク＋駅名）
  3. 駅ID で重複排除し、駅名→駅ID の対照表に整形して stations.json へマージ

注意:
  - area/0 は「首都圏の路線」一覧。路線は県境を越えるため、宇都宮/高崎 など
    一部 area=0 外の駅も混じる（通勤元になり得るので許容）。
  - 駅名は時刻表ページ表記から「駅」を除去（例: 東京駅→東京、大塚駅(東京)→大塚(東京)）。
  - robots.txt はクエリ無しの area/line/station パスを許可（確認済み）。

使い方:
    python harvest_tokyo_stations.py            # 全路線を収集して stations.json にマージ
    python harvest_tokyo_stations.py --limit 3  # 先頭3路線だけ（動作確認用）
    python harvest_tokyo_stations.py --out stations_full.json --delay 2
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://ekitan.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 "
    "(commute-cost-prototype; internal tool)"
)
DEFAULT_OUT = Path(__file__).resolve().parent / "stations.json"

_STATION_HREF = re.compile(r"/timetable/railway/station/(\d+)")
_LINE_HREF = re.compile(r"/timetable/railway/line/(\d+)")


def strip_eki(name: str) -> str:
    """「東京駅」→「東京」、「大塚駅(東京)」→「大塚(東京)」."""
    return re.sub(r"駅(?=\(|$)", "", name).strip()


def get(session: requests.Session, url: str) -> str:
    r = session.get(url, headers={"User-Agent": UA, "Referer": BASE + "/"}, timeout=25)
    r.raise_for_status()
    return r.text


def list_lines(session: requests.Session) -> list[tuple[str, str]]:
    """area/0 から (lineId, 路線名) を返す."""
    soup = BeautifulSoup(get(session, f"{BASE}/timetable/railway/area/0"), "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=_LINE_HREF):
        m = _LINE_HREF.search(a["href"])
        lid = m.group(1)
        if lid not in seen:
            seen.add(lid)
            out.append((lid, a.get_text(strip=True)))
    return out


def stations_of_line(session: requests.Session, line_id: str) -> list[tuple[str, str]]:
    """路線ページから (駅ID, 駅名) を返す（重複排除済み）."""
    soup = BeautifulSoup(get(session, f"{BASE}/timetable/railway/line/{line_id}"), "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=_STATION_HREF):
        m = _STATION_HREF.search(a["href"])
        sid = m.group(1)
        name = strip_eki(a.get_text(strip=True))
        if sid and name and sid not in seen:
            seen.add(sid)
            out.append((sid, name))
    return out


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="首都圏 全駅ID 一括収集")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--limit", type=int, default=0, help="先頭N路線だけ（0=全部）")
    p.add_argument("--delay", type=float, default=2.0, help="リクエスト間スリープ秒")
    args = p.parse_args(argv)

    session = requests.Session()
    lines = list_lines(session)
    if args.limit:
        lines = lines[: args.limit]
    print(f"路線数: {len(lines)}", flush=True)

    id_to_name: dict[str, str] = {}  # 駅ID -> 駅名（重複排除の基準は駅ID）
    for i, (lid, lname) in enumerate(lines, 1):
        time.sleep(args.delay)
        try:
            sts = stations_of_line(session, lid)
        except requests.RequestException as e:
            print(f"  [{i}/{len(lines)}] {lname} 取得失敗: {e}", file=sys.stderr, flush=True)
            continue
        new = 0
        for sid, name in sts:
            if sid not in id_to_name:
                id_to_name[sid] = name
                new += 1
        print(f"  [{i}/{len(lines)}] {lname}: {len(sts)}駅 (新規{new}) 累計{len(id_to_name)}", flush=True)

    # 駅名 -> 駅ID に反転。同名（区別不能）は警告して後勝ちを避ける。
    name_to_id: dict[str, str] = {}
    collisions = []
    for sid, name in id_to_name.items():
        if name in name_to_id and name_to_id[name] != sid:
            collisions.append((name, name_to_id[name], sid))
            continue  # 先に入った方を優先
        name_to_id[name] = sid

    # 既存 stations.json のキーをエイリアスとして温存（正しいIDに張り替え）
    out_path = Path(args.out)
    existing = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    final = dict(name_to_id)
    for k in existing:
        if k in final:
            continue  # 正式名に既にある
        cands = [n for n in name_to_id if n == k or n.startswith(k + "(")]
        if len(cands) == 1:
            final[k] = name_to_id[cands[0]]  # 例: 平井 -> 平井(東京) のID
            print(f"  エイリアス温存: {k} -> {final[k]}（{cands[0]}）", flush=True)
        else:
            print(f"  [警告] 既存キー '{k}' を一意解決できず、元値を保持", file=sys.stderr, flush=True)
            final[k] = existing[k]

    ordered = {k: final[k] for k in sorted(final)}
    out_path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n収集駅数(uniqID): {len(id_to_name)} / 書き出しキー数: {len(ordered)}")
    if collisions:
        print(f"同名衝突 {len(collisions)} 件（先勝ち採用、要確認）:", file=sys.stderr)
        for nm, a, b in collisions[:20]:
            print(f"   {nm}: {a} / {b}", file=sys.stderr)
    print(f"-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
