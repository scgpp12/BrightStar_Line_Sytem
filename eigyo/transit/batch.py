"""一括比較（Phase 3）.

ある現場（到着駅）に対し、複数の要員（出発駅＝最寄駅）の通勤コストを一括取得して
比較表にする。**同じ最寄駅は 1 回しか問い合わせない**（実行内メモ＋データソースの
ディスクキャッシュの二段でリクエストを節約）。これが請求を抑える肝。

業務層なので TransitDataSource / StationRegistry / CommuteResult にのみ依存し、
具象データソース（ekitan / ekispert）は知らない。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Iterable, Optional

from .config import SOURCE_REGISTRY_KEY
from .data_source import TransitDataSource
from .exceptions import TransitDataError
from .models import CommuteResult, Strategy
from .name_resolver import resolve_ekitan_id
from .registry import StationRegistry
from staff.models import Staff


def _resolve(station: str, registry: StationRegistry, reg_key: str) -> str:
    """ekitan はかな/ローマ字/日本語にあいまい対応、それ以外は対照表どおり."""
    if reg_key == "ekitan":
        return resolve_ekitan_id(station, registry)
    return registry.resolve(station, reg_key)


@dataclass
class StaffCommute:
    """要員 1 名ぶんの一括比較結果（成功なら result、失敗なら error）."""

    staff: Staff
    result: Optional[CommuteResult] = None
    error: Optional[str] = None


def compare_site(
    site: str,
    staffs: Iterable[Staff],
    source: TransitDataSource,
    registry: StationRegistry,
    strategy: "str | Strategy" = Strategy.CHEAPEST,
) -> list[StaffCommute]:
    """現場 `site` への、各要員の通勤コストを一括取得して並べる.

    - site / 各要員の最寄駅は registry でアクティブソースの駅IDに解決。
    - 同一 (from_id, to_id, strategy) は実行内メモで重複問い合わせしない。
    - 取得失敗した要員は error に理由を入れて返す（全体は止めない）。
    """
    strat = Strategy.from_str(strategy)
    reg_key = SOURCE_REGISTRY_KEY.get(source.name, "ekitan")
    to_id = _resolve(site, registry, reg_key)

    memo: dict[tuple[str, str, str], CommuteResult] = {}
    rows: list[StaffCommute] = []

    for staff in staffs:
        try:
            from_id = _resolve(staff.nearest_station, registry, reg_key)
            key = (from_id, to_id, strat.value)
            if key in memo:  # 同じ駅→キャッシュ命中、再問い合わせしない
                result = memo[key]
            else:
                result = source.query(from_id, to_id, strategy=strat)
                memo[key] = result
            rows.append(StaffCommute(staff=staff, result=result))
        except TransitDataError as e:
            rows.append(StaffCommute(staff=staff, error=str(e)))
        except Exception as e:  # noqa: BLE001 - 1名の失敗で全体を止めない
            rows.append(StaffCommute(staff=staff, error=f"{type(e).__name__}: {e}"))

    return _sort(rows, strat)


def _sort(rows: list[StaffCommute], strat: Strategy) -> list[StaffCommute]:
    """戦略順に並べる。エラー行は末尾。"""
    big = float("inf")

    def keyfn(r: StaffCommute):
        if r.result is None:
            return (1, big)  # エラーは最後
        if strat is Strategy.FASTEST:
            return (0, r.result.duration_min)
        return (0, r.result.pass_1month_yen)  # cheapest

    return sorted(rows, key=keyfn)


# --------------------------------------------------------------------- #
# 出力（CSV / 端末テーブル）
# --------------------------------------------------------------------- #
_CSV_HEADER = [
    "staff_id", "name", "department", "nearest_station", "site",
    "duration_min", "transfers", "fare_ic_yen", "fare_ticket_yen",
    "pass_1month_yen", "pass_3month_yen", "pass_6month_yen",
    "route_summary", "distance_km", "strategy", "source", "queried_at", "error",
]


def to_csv(site: str, rows: list[StaffCommute]) -> str:
    """比較結果を CSV 文字列に（Excel で開けるよう UTF-8）."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_CSV_HEADER)
    for r in rows:
        s, res = r.staff, r.result
        if res is None:
            w.writerow([s.staff_id, s.name, s.department or "", s.nearest_station,
                        site, "", "", "", "", "", "", "", "", "", "", "", "", r.error or ""])
        else:
            w.writerow([
                s.staff_id, s.name, s.department or "", s.nearest_station, site,
                res.duration_min, res.transfers, res.fare_ic_yen, res.fare_ticket_yen,
                res.pass_1month_yen, res.pass_3month_yen or "", res.pass_6month_yen or "",
                res.route_summary, res.distance_km if res.distance_km is not None else "",
                res.strategy, res.source, res.queried_at, "",
            ])
    return buf.getvalue()


def format_table(site: str, rows: list[StaffCommute], strategy: str) -> str:
    """端末用の比較表（等幅・日本語混在を考慮した簡易整形）."""
    lines = [f"■ 現場「{site}」への通勤コスト比較（{len(rows)}名 / 戦略: {strategy}）", ""]
    lines.append(
        f"{'ID':<5} {'氏名':<10} {'最寄駅':<12} {'時間':>5} {'乗換':>4} "
        f"{'IC片道':>7} {'定期1ヶ月':>9}  経由"
    )
    lines.append("-" * 78)
    for r in rows:
        s, res = r.staff, r.result
        if res is None:
            lines.append(f"{s.staff_id:<5} {_pad(s.name,10)} {_pad(s.nearest_station,12)} "
                         f"{'取得失敗: ' + (r.error or '')[:40]}")
            continue
        lines.append(
            f"{s.staff_id:<5} {_pad(s.name,10)} {_pad(s.nearest_station,12)} "
            f"{str(res.duration_min)+'分':>5} {str(res.transfers)+'回':>4} "
            f"{res.fare_ic_yen:>6,}円 {res.pass_1month_yen:>8,}円  {res.route_summary}"
        )
    # 集計（成功分のみ）
    ok = [r.result for r in rows if r.result]
    if ok:
        total = sum(r.pass_1month_yen for r in ok)
        lines += ["", f"定期代1ヶ月 合計（{len(ok)}名）: {total:,}円  /  平均: {total // len(ok):,}円"]
    return "\n".join(lines)


def _pad(text: str, width: int) -> str:
    """全角を2幅として概算でパディング（端末表示の桁ずれを軽減）."""
    w = sum(2 if ord(c) > 0x2E7F else 1 for c in text)
    return text + " " * max(0, width - w)
