"""DiskCache と同じインタフェースの DynamoDB 実装（Lambda 用）.

EkitanScraper はキャッシュを `get(namespace,key)->dict|None` / `set(namespace,key,payload)`
で使うだけなので、ディスクの代わりにこれを渡せば Lambda でも同じコードが動く
（差し替え契約と同じ考え方）。

テーブル想定: PK = cacheKey(文字列 "namespace#key")、payload(JSON文字列)、
cachedAt(epoch秒)、ttl(epoch秒, DynamoDB TTL で自動失効）。
"""

from __future__ import annotations

import json
import os
import time
from decimal import Decimal
from typing import Optional

import boto3

from .cache import DEFAULT_TTL_SEC


class DynamoDBCache:
    def __init__(self, table_name: Optional[str] = None, ttl_sec: int = DEFAULT_TTL_SEC):
        name = table_name or os.environ["CACHE_TABLE"]
        self.table = boto3.resource("dynamodb").Table(name)
        self.ttl_sec = ttl_sec

    @staticmethod
    def _pk(namespace: str, key: str) -> str:
        return f"{namespace}#{key}"

    def get(self, namespace: str, key: str) -> Optional[dict]:
        resp = self.table.get_item(Key={"cacheKey": self._pk(namespace, key)})
        item = resp.get("Item")
        if not item:
            return None
        cached_at = float(item.get("cachedAt", 0))
        if self.ttl_sec >= 0 and (time.time() - cached_at) > self.ttl_sec:
            return None  # 失効（DynamoDB TTL 削除前でもこちらで弾く）
        try:
            return json.loads(item["payload"])
        except (KeyError, json.JSONDecodeError):
            return None

    def set(self, namespace: str, key: str, payload: dict) -> str:
        now = time.time()
        pk = self._pk(namespace, key)
        self.table.put_item(
            Item={
                "cacheKey": pk,
                "payload": json.dumps(payload, ensure_ascii=False),
                "cachedAt": Decimal(str(int(now))),
                "ttl": int(now + self.ttl_sec) if self.ttl_sec >= 0 else 0,
            }
        )
        return pk
