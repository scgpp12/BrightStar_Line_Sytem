"""データソース非依存の戻り値モデル.

CommuteResult は「差し替え契約」の中心。ekitan だろうが駅すぱあと API だろうが、
どのデータソースも最終的にこの形を返す。ekitan 固有の概念（候補リスト構造・HTML 等）は
一切ここに漏らさない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Optional


class Strategy(str, Enum):
    """複数候補からどれを採るかの方針.

    - CHEAPEST: 通勤定期代（1ヶ月）が最安。同額なら所要時間が短い方。
    - FASTEST : 所要時間が最短。同時間なら定期代が安い方。
    """

    CHEAPEST = "cheapest"
    FASTEST = "fastest"

    @classmethod
    def from_str(cls, value: "str | Strategy") -> "Strategy":
        if isinstance(value, Strategy):
            return value
        try:
            return cls(value)
        except ValueError:
            valid = ", ".join(s.value for s in cls)
            raise ValueError(f"未知の戦略 '{value}'。使えるのは: {valid}")


@dataclass
class CommuteResult:
    """1 経路ぶんの通勤コスト調査結果（データソース共通の戻り値）."""

    from_station: str
    to_station: str
    duration_min: int          # 所要時間（分）
    transfers: int             # 乗換回数
    fare_ic_yen: int           # 片道 IC 運賃
    fare_ticket_yen: int       # 片道 きっぷ 運賃
    pass_1month_yen: int       # 通勤定期 1ヶ月

    # 取れれば取る任意項目
    pass_3month_yen: Optional[int] = None
    pass_6month_yen: Optional[int] = None
    route_summary: str = ""        # 経由（例: 平井→秋葉原→東京）
    distance_km: Optional[float] = None

    # メタ情報
    strategy: str = Strategy.CHEAPEST.value
    source: str = ""               # どのデータソースが返したか（例: "ekitan"）
    queried_at: str = ""           # データ取得時刻（ISO8601）。キャッシュ命中時は元の取得時刻
    from_cache: bool = False       # この結果がキャッシュ由来か

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int | None = None) -> str:
        # 日本語をそのまま出すため ensure_ascii=False
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def one_line(self) -> str:
        """提案資料の横並び比較用 1 行サマリ."""
        d3 = f"{self.pass_3month_yen:,}" if self.pass_3month_yen is not None else "-"
        d6 = f"{self.pass_6month_yen:,}" if self.pass_6month_yen is not None else "-"
        dist = f"{self.distance_km}km" if self.distance_km is not None else "-"
        cache = "  (cache)" if self.from_cache else ""
        return (
            f"{self.from_station} → {self.to_station} | "
            f"{self.duration_min}分 / 乗換{self.transfers}回 | "
            f"IC片道 {self.fare_ic_yen:,}円 / きっぷ {self.fare_ticket_yen:,}円 | "
            f"定期 1ヶ月 {self.pass_1month_yen:,}円 (3ヶ月 {d3} / 6ヶ月 {d6}) | "
            f"{dist} | 経由: {self.route_summary} | "
            f"[{self.strategy}/{self.source}]{cache}"
        )
