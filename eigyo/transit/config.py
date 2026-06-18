"""データソースの設定と組み立て（DATA_SOURCE 切り替えの一点）.

業務層（commute.py / 要員DB）は具象データソースを直接 import せず、ここの
build_source() 経由で受け取る。差し替えはこのファイルと環境変数だけで完結する。

環境変数:
  DATA_SOURCE        "ekitan_scraper"（既定）/ "ekispert_api"
  EKISPERT_API_KEY   ekispert_api を使う場合のみ必要（ハードコード禁止）
"""

from __future__ import annotations

import os

from .cache import DiskCache, DEFAULT_TTL_SEC
from .data_source import TransitDataSource

# データソース識別子 → このソースが解決に使う registry のソース名
SOURCE_REGISTRY_KEY = {
    "ekitan_scraper": "ekitan",
    "ekispert_api": "ekispert",
}

DEFAULT_DATA_SOURCE = "ekitan_scraper"


def get_data_source_name() -> str:
    return os.environ.get("DATA_SOURCE", DEFAULT_DATA_SOURCE)


def build_source(
    name: str | None = None,
    *,
    use_cache: bool = True,
    ttl_sec: int = DEFAULT_TTL_SEC,
    request_delay_sec: float = 3.0,
) -> TransitDataSource:
    """設定に従い TransitDataSource を 1 つ組み立てて返す."""
    name = name or get_data_source_name()
    cache = DiskCache(ttl_sec=ttl_sec)

    if name == "ekitan_scraper":
        # 遅延 import（ekispert だけ使う場合に bs4 等を読まないで済む）
        from .ekitan_source import EkitanScraper

        return EkitanScraper(
            cache=cache, request_delay_sec=request_delay_sec, use_cache=use_cache
        )

    if name == "ekispert_api":
        from .ekispert_source import EkispertApiSource

        return EkispertApiSource(cache=cache, use_cache=use_cache)

    valid = " / ".join(SOURCE_REGISTRY_KEY)
    raise ValueError(f"未知の DATA_SOURCE '{name}'。使えるのは: {valid}")
