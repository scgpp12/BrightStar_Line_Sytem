"""robots.txt の尊重.

クロール前に robots.txt を読み（ディスクにキャッシュ、既定 7 日）、
urllib.robotparser で can_fetch を確認する。

ekitan の robots.txt（2026-06 時点）では、運賃/定期ページの Disallow は
すべて「クエリ文字列付き」(`?*`) に対するもの:
    Disallow: /transit/fare?*
    Disallow: /transit/fare/*/*?*
    Disallow: /transit/pass?*
    Disallow: /transit/pass/*/*?*
よって本ツールが使う「クエリ無しの正準 URL」
    /transit/fare/sf-XXXX/st-YYYY
    /transit/pass/sf-XXXX/st-YYYY
は許可されている。コード側でも URL にクエリ文字列を絶対に付けないこと（下の
assert で担保）。詳細は README の robots.txt 節を参照。
"""

from __future__ import annotations

import time
import urllib.parse
from pathlib import Path
from urllib.robotparser import RobotFileParser

import requests

from .cache import DEFAULT_CACHE_DIR

_ROBOTS_TTL_SEC = 7 * 24 * 60 * 60  # 7 日


class RobotsGate:
    """robots.txt をキャッシュしつつ can_fetch を判定する."""

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        session: requests.Session,
        cache_dir: "str | Path" = DEFAULT_CACHE_DIR,
        ttl_sec: int = _ROBOTS_TTL_SEC,
    ):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.session = session
        self.ttl_sec = ttl_sec
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._robots_path = self.cache_dir / "robots.txt"
        self._parser: RobotFileParser | None = None

    def _load_text(self) -> str:
        """robots.txt 本文を取得（TTL 内ならディスクキャッシュから）."""
        if self._robots_path.exists():
            age = time.time() - self._robots_path.stat().st_mtime
            if age <= self.ttl_sec:
                return self._robots_path.read_text(encoding="utf-8")
        # 取り直し
        url = f"{self.base_url}/robots.txt"
        resp = self.session.get(url, timeout=20)
        resp.raise_for_status()
        self._robots_path.write_text(resp.text, encoding="utf-8")
        return resp.text

    def _ensure_parser(self) -> RobotFileParser:
        if self._parser is None:
            rp = RobotFileParser()
            try:
                rp.parse(self._load_text().splitlines())
            except requests.RequestException:
                # robots.txt が取れないときは保守的に「全部不許可」とはせず、
                # 取得失敗を呼び出し側に委ねる。ここでは空ルール=全許可にしておき、
                # 代わりにクエリ無し正準 URL のみ使う運用で安全側を担保する。
                rp.parse([])
            self._parser = rp
        return self._parser

    def can_fetch(self, url: str) -> bool:
        # 本ツールは決してクエリ文字列を付けない（robots の Disallow は ?* 限定のため）
        parsed = urllib.parse.urlsplit(url)
        assert not parsed.query, f"クエリ文字列付き URL は使わない約束: {url}"
        return self._ensure_parser().can_fetch(self.user_agent, url)
