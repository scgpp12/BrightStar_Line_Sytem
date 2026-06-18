"""要員保存層の抽象基底（= 保存の差し替え契約）.

業務層（一括比較など）はこの StaffRepository だけに依存する。
今は SQLiteStaffRepository を使うが、将来 BrightStar の DynamoDB roster を
読む BrightStarRosterRepository に差し替えても、同じインタフェースを実装すれば
業務層は 1 行も変えなくてよい。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Optional

from .models import Staff, StaffStatus


class StaffRepository(ABC):
    """要員の永続化インタフェース（CRUD + 一括更新）."""

    @abstractmethod
    def upsert(self, staff: Staff) -> None:
        """登録 or 更新（staff_id をキーに）."""

    @abstractmethod
    def get(self, staff_id: str) -> Optional[Staff]:
        """1 名取得（無ければ None）."""

    @abstractmethod
    def list(self, status: "str | StaffStatus | None" = None) -> list[Staff]:
        """全件 or ステータス絞り込みで一覧。"""

    @abstractmethod
    def update_status(
        self,
        staff_id: str,
        status: "str | StaffStatus",
        assigned_site: Optional[str] = None,
    ) -> Staff:
        """ステータス更新（assigned のとき現場名も任意で記録）。"""

    @abstractmethod
    def set_nearest_station(self, staff_id: str, nearest_station: str) -> Staff:
        """最寄駅を更新。"""

    @abstractmethod
    def delete(self, staff_id: str) -> None:
        """削除。"""

    @abstractmethod
    def bulk_upsert(self, staffs: Iterable[Staff]) -> int:
        """月末などの一括登録/更新の入口。件数を返す。"""
