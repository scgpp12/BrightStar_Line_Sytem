#!/usr/bin/env python3
"""花名册ファイル（CSV/txt）→ 要員DB 一括反映（増删改）.

BrightStar の「花名册主導」と同じ運用: 1つのファイルを正として要員DBへ反映する。
今後はこの txt/CSV を編集して流すだけで増删改ができる。

ファイル形式（UTF-8・カンマ区切り・1行目はヘッダ）:
    staff_id,name,nearest_station,address,department,status
    E002,拉拉,池袋,,営業部,available
    E010,田中,,東京都江東区平野3-1,営業部,available    ← address だけでも可（最寄駅に自動変換）

  - nearest_station と address はどちらか必須。両方あれば nearest_station 優先。
  - address は最寄駅に変換して保存し、**住所自体は保存しない**（個人情報配慮）。
  - 空行 / # で始まる行は無視。

使い方:
    python staff_sync.py roster.csv                      # SQLite に upsert（増・改）
    python staff_sync.py roster.csv --replace            # 完全同期（ファイルに無い要員は削除）
    python staff_sync.py roster.csv --dry-run            # 変更内容だけ表示
    python staff_sync.py roster.csv --target dynamo --table <StaffTable名>   # クラウドへ反映
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from staff import Staff, StaffStatus, SQLiteStaffRepository
from staff.repository import StaffRepository
from staff.sqlite_repository import DEFAULT_DB_PATH


def _build_repo(args: argparse.Namespace) -> StaffRepository:
    if args.target == "dynamo":
        from staff.dynamo_repository import DynamoDBStaffRepository
        return DynamoDBStaffRepository(table_name=args.table)
    return SQLiteStaffRepository(args.db)


def _parse_rows(path: Path) -> list[Staff]:
    text = path.read_text(encoding="utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    staffs: list[Staff] = []
    for i, row in enumerate(reader, 1):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        sid, name = row.get("staff_id"), row.get("name")
        if not sid or not name:
            raise ValueError(f"{path}:{i} 行目 staff_id と name は必須です: {row}")
        station = row.get("nearest_station")
        if not station and row.get("address"):
            from transit.geo import nearest_station as _nearest
            n = _nearest(row["address"])
            station = n["station"]
            print(f"  住所→最寄駅: {sid} {name} = {station}駅（{n['distance_m']}m）※住所は保存しません")
        if not station:
            raise ValueError(f"{path}:{i} 行目 nearest_station か address が必要です: {row}")
        staffs.append(Staff(
            staff_id=sid, name=name, nearest_station=station,
            status=StaffStatus.from_str(row.get("status") or "available"),
            department=row.get("department") or None,
        ))
    return staffs


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="花名册CSV → 要員DB 一括反映")
    p.add_argument("file", help="CSV/txt（UTF-8）")
    p.add_argument("--target", choices=["sqlite", "dynamo"], default="sqlite")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite のパス")
    p.add_argument("--table", default=None, help="DynamoDB テーブル名（既定: 環境変数 STAFF_TABLE）")
    p.add_argument("--replace", action="store_true", help="完全同期（ファイルに無い要員を削除）")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    staffs = _parse_rows(Path(args.file))
    file_ids = {s.staff_id for s in staffs}
    repo = _build_repo(args)
    existing = {s.staff_id: s for s in repo.list()}

    to_add = [s for s in staffs if s.staff_id not in existing]
    to_upd = [s for s in staffs if s.staff_id in existing]
    to_del = [sid for sid in existing if sid not in file_ids] if args.replace else []

    print(f"反映先: {args.target} / 追加{len(to_add)} 更新{len(to_upd)} 削除{len(to_del)}"
          + ("  [DRY-RUN]" if args.dry_run else ""))
    for s in to_add:
        print(f"  + {s.staff_id} {s.name}（{s.nearest_station}/{s.status.value}）")
    for s in to_upd:
        print(f"  ~ {s.staff_id} {s.name}（{s.nearest_station}/{s.status.value}）")
    for sid in to_del:
        print(f"  - {sid} {existing[sid].name}")

    if args.dry_run:
        print("（DRY-RUN のため未反映）")
        return 0

    repo.bulk_upsert(staffs)
    for sid in to_del:
        repo.delete(sid)
    print(f"反映完了: {len(staffs)} 件 upsert / {len(to_del)} 件 削除")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
