"""要員のデータモデル（保存層に依存しない）.

スキーマは BrightStar 社内システムの花名册(roster, PK=empId)に揃える:
  - staff_id  = roster の empId（E001…）。両DBの結合キー。
  - name / department = roster 由来（統合時に同期する）。
  - nearest_station / status = 通勤コスト試算のためにこのツールが足す項目。

個人情報配慮: 住所は持たず最寄駅のみ。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Optional


class StaffStatus(str, Enum):
    """要員の稼働ステータス."""

    AVAILABLE = "available"  # 待機（次の現場を探している）→ 一括比較の対象
    ASSIGNED = "assigned"    # 現場確定済み → 通常は比較対象から外す

    @classmethod
    def from_str(cls, value: "str | StaffStatus") -> "StaffStatus":
        if isinstance(value, StaffStatus):
            return value
        try:
            return cls(value)
        except ValueError:
            valid = ", ".join(s.value for s in cls)
            raise ValueError(f"未知のステータス '{value}'。使えるのは: {valid}")


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


@dataclass
class Staff:
    """要員 1 名."""

    staff_id: str             # = BrightStar roster empId（例 E002）
    name: str
    nearest_station: str      # 最寄駅（駅名 or 駅ID）。住所は持たない
    status: StaffStatus = StaffStatus.AVAILABLE
    department: Optional[str] = None   # roster 由来（営業部 など）
    assigned_site: Optional[str] = None  # assigned のときの現場（任意）
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class StaffNotFoundError(Exception):
    """指定の staff_id が見つからない."""
