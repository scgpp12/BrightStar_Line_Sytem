"""TransitDataSource の 駅すぱあと（ekispert）API 実装.

★差し替え契約★ EkitanScraper と同じ query() を実装し、同じ CommuteResult を返す。
上位層・要員DBはこのクラスを直接知らない（config.build_source 経由で選ばれるだけ）。

特性（重要）:
  無料枠では構造化の運賃/定期代が取れず URL しか返らないことがある。そこで本実装は
  **取れた項目だけ使い、取れない項目は None にしてログを出す（クラッシュしない）**。
  まず両ソースとも動かし、構造化データが取れると確認できたら API を主にする想定。

API:
  - key は環境変数 EKISPERT_API_KEY から読む（ハードコード禁止）。query 実行時に検証。
  - base: https://api.ekispert.jp/v1/json/
  - 経路探索: search/course/extreme（時間・乗換・距離・運賃・定期）
  - 駅名→駅コード: station/light（stations.json に ekispert コードが無い場合のフォールバック）
  - キャッシュは ekitan 実装と共通の DiskCache（TTL 30日）。

無料枠で 403 が出る場合の切り分けは README「ekispert の 403 対処」を参照
（ドメイン/IP バインド、key 未有効化、HTTPS 必須など）。
"""

from __future__ import annotations

import datetime
import os
import sys
import time
from typing import Any, Optional

import requests

from .cache import DiskCache
from .data_source import TransitDataSource
from .exceptions import RouteNotFoundError, TransitDataError
from .models import CommuteResult, Strategy

_BASE = "https://api.ekispert.jp/v1/json"
_CACHE_NS = "ekispert_routes"
_STATION_NS = "ekispert_station"


def _to_int(value: Any) -> Optional[int]:
    """数字文字列を int に。失敗時は None（堅牢性のため例外にしない）."""
    if value is None:
        return None
    try:
        return int(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _as_list(value: Any) -> list:
    """ekispert JSON は要素1個だと dict、複数だと list になる。常に list に正規化."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class EkispertApiSource(TransitDataSource):
    """駅すぱあと API を叩く TransitDataSource（無料枠フォールバック対応）."""

    name = "ekispert"

    def __init__(
        self,
        cache: Optional[DiskCache] = None,
        request_delay_sec: float = 1.0,
        timeout_sec: int = 25,
        use_cache: bool = True,
    ):
        self.cache = cache or DiskCache()
        self.request_delay_sec = request_delay_sec
        self.timeout_sec = timeout_sec
        self.use_cache = use_cache
        self.session = requests.Session()

    # ------------------------------------------------------------------ #
    # 公開 API（TransitDataSource 契約）
    # ------------------------------------------------------------------ #
    def query(
        self,
        from_id: str,
        to_id: str,
        strategy: "str | Strategy" = Strategy.CHEAPEST,
    ) -> CommuteResult:
        strat = Strategy.from_str(strategy)
        key = self._require_key()

        # 駅コード解決（数字以外＝駅名はAPIで解決）
        from_code, from_name = self._resolve_station(from_id, key)
        to_code, to_name = self._resolve_station(to_id, key)

        cache_key = f"{from_code}_{to_code}"
        bundle = None
        from_cache = False
        if self.use_cache:
            cached = self.cache.get(_CACHE_NS, cache_key)
            if cached is not None:
                bundle = cached
                from_cache = True

        if bundle is None:
            bundle = self._fetch_and_parse(from_code, to_code, from_name, to_name, key)
            self.cache.set(_CACHE_NS, cache_key, bundle)

        candidate = self._select(bundle["candidates"], strat)
        if candidate is None:
            raise RouteNotFoundError(
                f"{from_code}->{to_code}: 経路候補が取得できませんでした（ekispert）"
            )

        return CommuteResult(
            from_station=bundle["from_station"],
            to_station=bundle["to_station"],
            duration_min=candidate.get("duration_min") or 0,
            transfers=candidate.get("transfers") or 0,
            fare_ic_yen=candidate.get("fare_ic_yen") or 0,
            fare_ticket_yen=candidate.get("fare_ticket_yen") or 0,
            pass_1month_yen=candidate.get("pass_1month_yen") or 0,
            pass_3month_yen=candidate.get("pass_3month_yen"),
            pass_6month_yen=candidate.get("pass_6month_yen"),
            route_summary=candidate.get("route_summary", ""),
            distance_km=candidate.get("distance_km"),
            strategy=strat.value,
            source=self.name,
            queried_at=bundle["queried_at"],
            from_cache=from_cache,
        )

    # ------------------------------------------------------------------ #
    # 下請け
    # ------------------------------------------------------------------ #
    def _require_key(self) -> str:
        key = os.environ.get("EKISPERT_API_KEY")
        if not key:
            raise TransitDataError(
                "環境変数 EKISPERT_API_KEY が未設定です。"
                "駅すぱあとの key を設定するか、DATA_SOURCE=ekitan_scraper を使ってください。"
            )
        return key

    def _get(self, path: str, params: dict) -> dict:
        params = {**params, "key": params.get("key")}
        resp = self.session.get(
            f"{_BASE}/{path}", params=params, timeout=self.timeout_sec
        )
        if resp.status_code == 403:
            raise TransitDataError(
                f"ekispert 403 Forbidden（{path}）。key 未有効化/ドメイン・IPバインド/"
                "無料枠制限の可能性。README『ekispert の 403 対処』参照。"
            )
        if resp.status_code != 200:
            raise TransitDataError(f"ekispert HTTP {resp.status_code}: {path}")
        try:
            return resp.json()
        except ValueError as e:
            raise TransitDataError(f"ekispert JSON 解析失敗（{path}）: {e}")

    def _resolve_station(self, identifier: str, key: str) -> tuple[str, str]:
        """駅指定子（コード or 駅名）→ (駅コード, 駅名)。

        数字ならコードとみなしそのまま。駅名なら station/light で解決（キャッシュ）。
        """
        identifier = identifier.strip()
        if identifier.isdigit():
            return identifier, identifier  # コード直指定（名前は後で route から補完可）

        if self.use_cache:
            cached = self.cache.get(_STATION_NS, identifier)
            if cached:
                return cached["code"], cached["name"]

        data = self._get("station/light", {"key": key, "name": identifier})
        time.sleep(self.request_delay_sec)
        stations = _as_list((data.get("ResultSet") or {}).get("Point")) or _as_list(
            (data.get("ResultSet") or {}).get("Station")
        )
        if not stations:
            raise TransitDataError(
                f"ekispert で駅 '{identifier}' を解決できませんでした（station/light）"
            )
        st = stations[0]
        # Point -> {"Station": {...}} / Station -> {...} の両形に対応
        station = st.get("Station", st) if isinstance(st, dict) else {}
        code = str(station.get("code") or st.get("code") or "")
        name = str(station.get("Name") or station.get("name") or identifier)
        if not code:
            raise TransitDataError(f"ekispert 駅コードが取れません: {identifier}")
        result = {"code": code, "name": name}
        self.cache.set(_STATION_NS, identifier, result)
        return code, name

    def _fetch_and_parse(
        self, from_code: str, to_code: str, from_name: str, to_name: str, key: str
    ) -> dict:
        data = self._get(
            "search/course/extreme",
            {"key": key, "viaList": f"{from_code}:{to_code}"},
        )
        time.sleep(self.request_delay_sec)

        courses = _as_list((data.get("ResultSet") or {}).get("Course"))
        candidates: list[dict] = []
        for c in courses:
            try:
                candidates.append(self._parse_course(c))
            except Exception as e:  # noqa: BLE001 - 堅牢性優先。1経路の失敗で全体を落とさない
                print(f"[警告] ekispert 経路の解析に一部失敗: {e}", file=sys.stderr)

        # from/to 名は最初の経路から補完できれば差し替え
        if candidates:
            from_name = candidates[0].get("from_station") or from_name
            to_name = candidates[0].get("to_station") or to_name

        if not candidates:
            raise RouteNotFoundError(
                f"{from_code}->{to_code}: ekispert から経路を取得できませんでした"
            )

        return {
            "queried_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "from_station": from_name,
            "to_station": to_name,
            "candidates": candidates,
        }

    def _parse_course(self, course: dict) -> dict:
        """1 経路を解析。取れない項目は None（無料枠で URL のみ等に耐える）."""
        route = course.get("Route") or {}

        # 所要時間（分）: 乗車+徒歩+その他の合計（取れるものだけ）
        mins = [
            _to_int(route.get(k))
            for k in ("timeOnBoard", "timeWalk", "timeOther")
        ]
        mins = [m for m in mins if m is not None]
        duration_min = sum(mins) if mins else None

        transfers = _to_int(route.get("transferCount"))
        distance_km = None
        d = _to_int(route.get("distance"))
        if d is not None:
            distance_km = round(d / 10.0, 1)  # ekispert distance は 0.1km 単位

        # 経由（駅名の並び）
        points = _as_list(route.get("Point"))
        names = []
        for p in points:
            st = p.get("Station") if isinstance(p, dict) else None
            nm = (st or {}).get("Name") if isinstance(st, dict) else None
            if nm:
                names.append(nm)
        route_summary = "→".join(names)
        from_station = names[0] if names else None
        to_station = names[-1] if names else None

        # 運賃・定期代
        prices = self._extract_prices(_as_list(course.get("Price")))

        return {
            "duration_min": duration_min,
            "transfers": transfers,
            "distance_km": distance_km,
            "route_summary": route_summary,
            "from_station": from_station,
            "to_station": to_station,
            "fare_ic_yen": prices.get("ic"),
            "fare_ticket_yen": prices.get("ticket"),
            "pass_1month_yen": prices.get("teiki1"),
            "pass_3month_yen": prices.get("teiki3"),
            "pass_6month_yen": prices.get("teiki6"),
        }

    @staticmethod
    def _extract_prices(price_list: list) -> dict:
        """Price[] から IC片道/きっぷ片道/通勤定期1・3・6ヶ月を best-effort 抽出.

        ekispert の Price は @kind / @name 等の表記揺れがあるため、kind/name を
        小文字化して部分一致で振り分ける。取れなければ該当キー無し（→None 扱い）。
        通学(university/college)・オフピーク(offpeak) は通勤ではないので除外。
        """
        out: dict[str, int] = {}
        for p in price_list:
            if not isinstance(p, dict):
                continue
            kind = str(p.get("kind", "")).lower()
            pname = str(p.get("name", "")).lower()
            tag = kind + " " + pname
            oneway = _to_int(p.get("Oneway") or p.get("oneway") or p.get("price"))
            if oneway is None:
                continue

            is_teiki = "teiki" in kind or "定期" in pname or "commut" in tag
            if is_teiki:
                if "univ" in tag or "学" in tag or "offpeak" in tag or "off-peak" in tag:
                    continue  # 通学・オフピークは除外（通勤のみ）
                if "1" in tag and "teiki1" not in out:
                    out["teiki1"] = oneway
                elif "3" in tag:
                    out["teiki3"] = oneway
                elif "6" in tag:
                    out["teiki6"] = oneway
                continue

            # 通常運賃: IC か きっぷ か
            if "ic" in tag:
                out.setdefault("ic", oneway)
            elif "fare" in kind or "運賃" in pname or "切符" in pname or "きっぷ" in pname:
                out.setdefault("ticket", oneway)
        return out

    @staticmethod
    def _select(candidates: list[dict], strat: Strategy) -> Optional[dict]:
        """戦略で1経路選ぶ。無料枠で定期/運賃が None でも所要時間で選べるよう寛容に."""
        usable = [c for c in candidates if c.get("duration_min") is not None]
        if not usable:
            usable = candidates  # 所要時間すら無ければ全件対象（先頭採用）
        if not usable:
            return None

        big = float("inf")
        if strat is Strategy.CHEAPEST:
            # 定期1ヶ月優先、無ければIC運賃、それも無ければ所要時間で代替
            def keyfn(c: dict):
                cost = c.get("pass_1month_yen")
                if cost is None:
                    cost = c.get("fare_ic_yen")
                return (cost if cost is not None else big, c.get("duration_min") or big)

            return min(usable, key=keyfn)

        # FASTEST
        return min(
            usable,
            key=lambda c: (
                c.get("duration_min") or big,
                c.get("pass_1month_yen") if c.get("pass_1month_yen") is not None else big,
            ),
        )
