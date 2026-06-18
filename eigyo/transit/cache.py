"""ローカルディスクキャッシュ（このツールがリクエストを節約する肝）.

同じ (from_id, to_id) を一度引いたら、解析済みの候補リストを JSON で保存する。
既定では TTL（既定 30 日）内ならキャッシュを返し、一切リクエストを出さない。
十数人規模の内部ツールなので、総リクエスト量はこれで十分低く保てる。

戦略（cheapest/fastest）はキャッシュ済み候補に対してその場で適用する。
よって戦略を切り替えても再取得は起きない（キャッシュキーは from/to のみ）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

# 既定キャッシュ場所: パッケージの 1 つ上（プロジェクト直下）の .cache/
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
DEFAULT_TTL_SEC = 30 * 24 * 60 * 60  # 30 日


class DiskCache:
    def __init__(
        self,
        cache_dir: "str | Path" = DEFAULT_CACHE_DIR,
        ttl_sec: int = DEFAULT_TTL_SEC,
    ):
        self.cache_dir = Path(cache_dir)
        self.ttl_sec = ttl_sec
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str) -> Path:
        # キーは英数のみ想定（駅IDなので安全）。一応サニタイズ。
        safe = "".join(c if c.isalnum() else "_" for c in key)
        return self.cache_dir / f"{namespace}__{safe}.json"

    def get(self, namespace: str, key: str) -> Optional[dict]:
        """TTL 内なら保存済みペイロードを返す。無ければ / 失効なら None."""
        p = self._path(namespace, key)
        if not p.exists():
            return None
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None  # 壊れたキャッシュは無視して取り直す
        cached_at = blob.get("_cached_at", 0)
        if self.ttl_sec >= 0 and (time.time() - cached_at) > self.ttl_sec:
            return None  # 失効
        return blob.get("payload")

    def set(self, namespace: str, key: str, payload: dict) -> Path:
        p = self._path(namespace, key)
        blob = {"_cached_at": time.time(), "payload": payload}
        p.write_text(
            json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return p
