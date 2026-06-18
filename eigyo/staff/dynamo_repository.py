"""StaffRepository の DynamoDB 実装（Lambda 用）.

SQLiteStaffRepository と同じインタフェース。テーブル PK = staff_id(=empId)。
将来 BrightStar roster と統合する際もこのクラスを差し替え/拡張するだけで済む。
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

import boto3

from .models import Staff, StaffStatus, StaffNotFoundError, _now
from .repository import StaffRepository


class DynamoDBStaffRepository(StaffRepository):
    def __init__(self, table_name: Optional[str] = None):
        name = table_name or os.environ["STAFF_TABLE"]
        self.table = boto3.resource("dynamodb").Table(name)

    @staticmethod
    def _to_staff(item: dict) -> Staff:
        return Staff(
            staff_id=item["staff_id"],
            name=item["name"],
            nearest_station=item["nearest_station"],
            status=StaffStatus.from_str(item.get("status", "available")),
            department=item.get("department"),
            assigned_site=item.get("assigned_site"),
            updated_at=item.get("updated_at", ""),
        )

    @staticmethod
    def _to_item(s: Staff) -> dict:
        item = {
            "staff_id": s.staff_id,
            "name": s.name,
            "nearest_station": s.nearest_station,
            "status": s.status.value,
            "updated_at": s.updated_at or _now(),
        }
        # DynamoDB は None を嫌うので、値があるときだけ入れる
        if s.department:
            item["department"] = s.department
        if s.assigned_site:
            item["assigned_site"] = s.assigned_site
        return item

    def upsert(self, staff: Staff) -> None:
        self.table.put_item(Item=self._to_item(staff))

    def get(self, staff_id: str) -> Optional[Staff]:
        resp = self.table.get_item(Key={"staff_id": staff_id})
        item = resp.get("Item")
        return self._to_staff(item) if item else None

    def list(self, status: "str | StaffStatus | None" = None) -> list[Staff]:
        # 規模が小さい（十数人）ので全件 Scan で十分
        items = self.table.scan().get("Items", [])
        staffs = [self._to_staff(i) for i in items]
        if status is not None:
            st = StaffStatus.from_str(status)
            staffs = [s for s in staffs if s.status is st]
        return sorted(staffs, key=lambda s: s.staff_id)

    def update_status(
        self,
        staff_id: str,
        status: "str | StaffStatus",
        assigned_site: Optional[str] = None,
    ) -> Staff:
        st = StaffStatus.from_str(status)
        staff = self.get(staff_id)
        if staff is None:
            raise StaffNotFoundError(f"要員 '{staff_id}' が見つかりません")
        staff.status = st
        staff.assigned_site = assigned_site if st is StaffStatus.ASSIGNED else None
        staff.updated_at = _now()
        self.upsert(staff)
        return staff

    def set_nearest_station(self, staff_id: str, nearest_station: str) -> Staff:
        staff = self.get(staff_id)
        if staff is None:
            raise StaffNotFoundError(f"要員 '{staff_id}' が見つかりません")
        staff.nearest_station = nearest_station
        staff.updated_at = _now()
        self.upsert(staff)
        return staff

    def delete(self, staff_id: str) -> None:
        self.table.delete_item(Key={"staff_id": staff_id})

    def bulk_upsert(self, staffs: Iterable[Staff]) -> int:
        n = 0
        with self.table.batch_writer() as bw:
            for s in staffs:
                bw.put_item(Item=self._to_item(s))
                n += 1
        return n
