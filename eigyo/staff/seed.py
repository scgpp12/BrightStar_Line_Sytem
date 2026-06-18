"""サンプル要員データ（BrightStar 花名册 roster に揃える）.

staff_id / name / department は BrightStar の demo roster（E001…）と一致させ、
統合時にそのまま結合できる形にしてある。nearest_station / status は本ツール固有。
最寄駅は同梱 stations.json に存在する駅名を使用。
"""

from __future__ import annotations

from .models import Staff, StaffStatus

# BrightStar demo roster: E001 孫成功/人事部/hr, E002 拉拉/営業部, E003 山田太郎,
# E004 佐藤花子, E005 鈴木一郎。営業部の4名を派遣対象の要員として登録。
SAMPLE_STAFF: list[Staff] = [
    Staff("E002", "拉拉", nearest_station="池袋", status=StaffStatus.AVAILABLE, department="営業部"),
    Staff("E003", "山田太郎", nearest_station="横浜", status=StaffStatus.AVAILABLE, department="営業部"),
    Staff("E004", "佐藤花子", nearest_station="大宮(埼玉)", status=StaffStatus.ASSIGNED,
          department="営業部", assigned_site="東京"),
    Staff("E005", "鈴木一郎", nearest_station="立川", status=StaffStatus.AVAILABLE, department="営業部"),
]
