#!/usr/bin/env python3
"""要員DB 管理 CLI（Phase 2）.

SQLite の要員DBに対する増删改查 + 月末の一括更新の入口。UI は無し（CLI/関数のみ）。

使い方:
    python staff_admin.py seed                         # サンプル要員を投入（BrightStar roster準拠）
    python staff_admin.py list                         # 全員
    python staff_admin.py list --status available      # 待機中だけ
    python staff_admin.py add E006 田中次郎 川口 --dept 営業部
    python staff_admin.py status E002 assigned --site 東京   # 現場確定
    python staff_admin.py status E004 available              # 待機に戻す
    python staff_admin.py station E003 品川                   # 最寄駅変更
    python staff_admin.py delete E006
"""

from __future__ import annotations

import argparse
import sys

from staff import Staff, StaffStatus, StaffNotFoundError, SQLiteStaffRepository
from staff.seed import SAMPLE_STAFF


def _print(staff: Staff) -> None:
    site = f" @現場:{staff.assigned_site}" if staff.assigned_site else ""
    dept = f" / {staff.department}" if staff.department else ""
    print(
        f"  {staff.staff_id}  {staff.name}{dept} | 最寄:{staff.nearest_station} | "
        f"{staff.status.value}{site} | 更新:{staff.updated_at}"
    )


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="要員DB 管理（SQLite）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed", help="サンプル要員を一括投入")

    lp = sub.add_parser("list", help="一覧")
    lp.add_argument("--status", choices=[s.value for s in StaffStatus])

    ap = sub.add_parser("add", help="要員追加（最寄駅 または --address で住所から自動解決）")
    ap.add_argument("staff_id")
    ap.add_argument("name")
    ap.add_argument("nearest_station", nargs="?", default=None, help="最寄駅（--address 指定時は省略可）")
    ap.add_argument("--address", default=None, help="住所（最寄駅に変換して保存。住所自体は保存しない）")
    ap.add_argument("--dept", default=None)
    ap.add_argument("--status", choices=[s.value for s in StaffStatus], default="available")

    sp = sub.add_parser("status", help="ステータス更新")
    sp.add_argument("staff_id")
    sp.add_argument("status", choices=[s.value for s in StaffStatus])
    sp.add_argument("--site", default=None, help="assigned のときの現場名")

    stp = sub.add_parser("station", help="最寄駅変更")
    stp.add_argument("staff_id")
    stp.add_argument("nearest_station")

    dp = sub.add_parser("delete", help="削除")
    dp.add_argument("staff_id")

    args = p.parse_args(argv)
    repo = SQLiteStaffRepository()

    try:
        if args.cmd == "seed":
            n = repo.bulk_upsert(SAMPLE_STAFF)
            print(f"サンプル要員 {n} 件を投入しました。")
            for s in repo.list():
                _print(s)

        elif args.cmd == "list":
            staffs = repo.list(args.status)
            label = f"（{args.status}）" if args.status else "（全員）"
            print(f"要員一覧{label}: {len(staffs)}名")
            for s in staffs:
                _print(s)

        elif args.cmd == "add":
            station = args.nearest_station
            if not station and args.address:
                from transit.geo import nearest_station as _nearest
                n = _nearest(args.address)
                station = n["station"]
                print(f"住所 → 最寄駅: {station}駅（{n['distance_m']}m）※住所は保存しません")
            if not station:
                print("最寄駅 か --address のどちらかが必要です", file=sys.stderr)
                return 1
            repo.upsert(
                Staff(
                    staff_id=args.staff_id,
                    name=args.name,
                    nearest_station=station,
                    status=StaffStatus.from_str(args.status),
                    department=args.dept,
                )
            )
            print("追加/更新しました:")
            _print(repo.get(args.staff_id))

        elif args.cmd == "status":
            s = repo.update_status(args.staff_id, args.status, assigned_site=args.site)
            print("更新しました:")
            _print(s)

        elif args.cmd == "station":
            s = repo.set_nearest_station(args.staff_id, args.nearest_station)
            print("更新しました:")
            _print(s)

        elif args.cmd == "delete":
            repo.delete(args.staff_id)
            print(f"{args.staff_id} を削除しました。")

    except StaffNotFoundError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
