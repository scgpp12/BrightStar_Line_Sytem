"""Lambda Function URL ハンドラ（要員通勤コスト API）.

同じ transit / staff のコードを、キャッシュ=DynamoDB・要員DB=DynamoDB に差し替えて動かす
（ローカルCLIと業務ロジックは共通。差し替え契約のおかげで再実装不要）。

ルート（GET 中心。Function URL ペイロード v2.0）:
  GET  /                       使い方
  GET  /query?from=&to=&strategy=          単一区間
  GET  /site?site=&strategy=&status=&format=  現場→要員一括比較（format=csv で CSV）
  GET  /staff?status=                      要員一覧
  POST /seed                               サンプル要員を投入（初回用）

注意: ekitan スクレイピングはリクエスト間に sleep する。キャッシュ未命中の駅が多いと
時間がかかる（Lambda timeout を長めに設定）。同一駅はキャッシュ＋実行内メモで1回だけ。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from transit.registry import StationRegistry
from transit.ekitan_source import EkitanScraper
from transit.dynamo_cache import DynamoDBCache
from transit.batch import compare_site, to_csv
from transit.models import Strategy
from transit.name_resolver import resolve_ekitan_id
from transit.geo import nearest_station
from staff.dynamo_repository import DynamoDBStaffRepository
from staff.models import Staff, StaffStatus
from staff.seed import SAMPLE_STAFF

_STATIONS_PATH = Path(__file__).resolve().parent / "stations.json"
_REGISTRY = StationRegistry.from_file(_STATIONS_PATH)  # コールド時に1回


def _source() -> EkitanScraper:
    return EkitanScraper(
        cache=DynamoDBCache(),
        request_delay_sec=float(os.environ.get("REQUEST_DELAY_SEC", "3")),
        robots_cache_dir="/tmp/robots",
    )


def _repo() -> DynamoDBStaffRepository:
    return DynamoDBStaffRepository()


def _json(status: int, body, *, raw_text: str | None = None, content_type=None) -> dict:
    if raw_text is not None:
        return {
            "statusCode": status,
            "headers": {"content-type": content_type or "text/plain; charset=utf-8"},
            "body": raw_text,
        }
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json; charset=utf-8"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def handler(event: dict, context) -> dict:
    method = (event.get("requestContext", {}).get("http", {}) or {}).get("method", "GET")
    path = event.get("rawPath", "/")
    qs = event.get("queryStringParameters") or {}

    try:
        if path == "/" or path == "":
            return _json(200, {
                "service": "commute-cost-api",
                "routes": {
                    "GET /query": "from, to, strategy(cheapest|fastest)",
                    "GET /site": "site, strategy, status(available|assigned|all), format(json|csv)",
                    "GET /staff": "status(available|assigned)",
                    "GET /nearest": "address → 最寄駅",
                    "POST /staff": "{staff_id,name,nearest_station|address,department,status}",
                    "POST /seed": "サンプル要員投入",
                },
            })

        if path == "/query" and method == "GET":
            frm, to = qs.get("from"), qs.get("to")
            if not frm or not to:
                return _json(400, {"error": "from と to は必須です"})
            strat = Strategy.from_str(qs.get("strategy", "cheapest"))
            from_id = _REGISTRY.resolve(frm, "ekitan")
            to_id = _REGISTRY.resolve(to, "ekitan")
            result = _source().query(from_id, to_id, strategy=strat)
            return _json(200, result.to_dict())

        if path == "/site" and method == "GET":
            site = qs.get("site")
            if not site:
                return _json(400, {"error": "site は必須です"})
            strat = Strategy.from_str(qs.get("strategy", "cheapest"))
            status = qs.get("status", "available")
            repo = _repo()
            staffs = repo.list() if status == "all" else repo.list(StaffStatus.from_str(status))
            rows = compare_site(site, staffs, _source(), _REGISTRY, strategy=strat)
            if qs.get("format") == "csv":
                return _json(200, None, raw_text=to_csv(site, rows),
                             content_type="text/csv; charset=utf-8")
            return _json(200, {
                "site": site,
                "strategy": strat.value,
                "count": len(rows),
                "results": [
                    {
                        "staff": r.staff.to_dict(),
                        "commute": r.result.to_dict() if r.result else None,
                        "error": r.error,
                    }
                    for r in rows
                ],
            })

        if path == "/staff" and method == "GET":
            status = qs.get("status")
            repo = _repo()
            staffs = repo.list(StaffStatus.from_str(status)) if status else repo.list()
            return _json(200, {"count": len(staffs), "staff": [s.to_dict() for s in staffs]})

        if path == "/nearest" and method == "GET":
            address = qs.get("address")
            if not address:
                return _json(400, {"error": "address は必須です"})
            n = nearest_station(address)
            n["ekitan_id"] = resolve_ekitan_id(n["station"], _REGISTRY)
            return _json(200, n)

        if path == "/staff" and method == "POST":
            try:
                data = json.loads(event.get("body") or "{}")
            except json.JSONDecodeError:
                return _json(400, {"error": "JSON ボディが不正です"})
            if not data.get("staff_id") or not data.get("name"):
                return _json(400, {"error": "staff_id と name は必須です"})
            station = data.get("nearest_station")
            # 住所が来たら最寄駅に変換して保存（住所自体は保存しない＝個人情報配慮）
            if not station and data.get("address"):
                station = nearest_station(data["address"])["station"]
            if not station:
                return _json(400, {"error": "nearest_station か address のどちらかが必要です"})
            staff = Staff(
                staff_id=data["staff_id"],
                name=data["name"],
                nearest_station=station,
                status=StaffStatus.from_str(data.get("status", "available")),
                department=data.get("department"),
            )
            _repo().upsert(staff)
            return _json(200, {"saved": staff.to_dict()})

        if path == "/seed" and method == "POST":
            n = _repo().bulk_upsert(SAMPLE_STAFF)
            return _json(200, {"seeded": n})

        return _json(404, {"error": f"未知のルート: {method} {path}"})

    except Exception as e:  # noqa: BLE001 - API は必ず JSON で返す
        return _json(500, {"error": f"{type(e).__name__}: {e}"})
