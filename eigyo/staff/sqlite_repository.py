"""StaffRepository の SQLite 実装（標準ライブラリのみ・ゼロ依存）.

この規模（要員十数人）なら SQLite で十分。ファイル 1 個で完結し、外部依存も無い。
将来 BrightStar DynamoDB roster と統合する際は、同じ StaffRepository を実装した
別クラスに差し替えるだけ（業務層は無改修）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .models import Staff, StaffStatus, StaffNotFoundError, _now
from .repository import StaffRepository

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "staff.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS staff (
    staff_id        TEXT PRIMARY KEY,   -- = BrightStar roster empId
    name            TEXT NOT NULL,
    nearest_station TEXT NOT NULL,      -- 最寄駅のみ（住所は持たない）
    status          TEXT NOT NULL,      -- available | assigned
    department      TEXT,               -- roster 由来
    assigned_site   TEXT,
    updated_at      TEXT NOT NULL
);
"""


class SQLiteStaffRepository(StaffRepository):
    def __init__(self, db_path: "str | Path" = DEFAULT_DB_PATH):
        self.db_path = str(db_path)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_staff(r: sqlite3.Row) -> Staff:
        return Staff(
            staff_id=r["staff_id"],
            name=r["name"],
            nearest_station=r["nearest_station"],
            status=StaffStatus.from_str(r["status"]),
            department=r["department"],
            assigned_site=r["assigned_site"],
            updated_at=r["updated_at"],
        )

    # ----- CRUD ----------------------------------------------------- #
    def upsert(self, staff: Staff) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO staff
                    (staff_id, name, nearest_station, status, department, assigned_site, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(staff_id) DO UPDATE SET
                    name=excluded.name,
                    nearest_station=excluded.nearest_station,
                    status=excluded.status,
                    department=excluded.department,
                    assigned_site=excluded.assigned_site,
                    updated_at=excluded.updated_at
                """,
                (
                    staff.staff_id,
                    staff.name,
                    staff.nearest_station,
                    staff.status.value,
                    staff.department,
                    staff.assigned_site,
                    staff.updated_at or _now(),
                ),
            )

    def get(self, staff_id: str) -> Optional[Staff]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM staff WHERE staff_id = ?", (staff_id,)
            ).fetchone()
        return self._row_to_staff(row) if row else None

    def list(self, status: "str | StaffStatus | None" = None) -> list[Staff]:
        with self._conn() as c:
            if status is None:
                rows = c.execute("SELECT * FROM staff ORDER BY staff_id").fetchall()
            else:
                st = StaffStatus.from_str(status).value
                rows = c.execute(
                    "SELECT * FROM staff WHERE status = ? ORDER BY staff_id", (st,)
                ).fetchall()
        return [self._row_to_staff(r) for r in rows]

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
        # assigned に変わるときだけ現場名を記録、available に戻すときはクリア
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
        with self._conn() as c:
            cur = c.execute("DELETE FROM staff WHERE staff_id = ?", (staff_id,))
            if cur.rowcount == 0:
                raise StaffNotFoundError(f"要員 '{staff_id}' が見つかりません")

    def bulk_upsert(self, staffs: Iterable[Staff]) -> int:
        n = 0
        for s in staffs:
            self.upsert(s)
            n += 1
        return n
