"""TransitDataSource の ekitan（駅探）実装.

★重要★ ekitan 固有の知識（URL 形式・HTML/JSON 構造・候補リストの並び）は
すべてこのファイルに閉じ込める。外（上位層・CommuteResult）には漏らさない。

データの取り方（2026-06 実地確認済み・サーバサイドレンダリングで requests だけで取れる）:
  - 運賃/時間/換乗: GET /transit/fare/sf-{from}/st-{to}
        各候補 <div class="ek-route" data-ek-route-json='{...}'> に
        所要時間・乗換・走行距離・経由駅・路線が JSON で入っている（ページ自身のデータモデル）。
        運賃は同 div 内 <span class="ek-ticket_total">（きっぷ片道）/<span class="ek-ic_total">（IC片道）。
  - 定期代: GET /transit/pass/sf-{from}/st-{to}
        定期ページに route-json は無い。通勤定期は data-ek-display="business"。
        <div class="result-route-commutation-wrap" data-ek-display="business"> に
        通勤 1ヶ月/3ヶ月/6ヶ月 が入る（通学=college等, オフピーク=businessOffPeak は使わない）。
        経路の対応付けは上部 <tr class="ek-summary"> の 路線パス+所要時間 で行う。

  運賃ページと定期ページは別クエリなので、(路線シグネチャ, 所要分) をキーに突き合わせて
  1 経路に統合する。
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .cache import DiskCache
from .data_source import TransitDataSource
from .exceptions import ParseError, RouteNotFoundError, TransitDataError
from .models import CommuteResult, Strategy
from .robots import RobotsGate

_BASE = "https://ekitan.com"
# 実在ブラウザ相当の User-Agent（連絡先を添えて素性を明らかにする）
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 "
    "(commute-cost-prototype; internal tool)"
)
_CACHE_NS = "ekitan_routes"


def _clean_int(text: Optional[str], field: str, snippet: str = "") -> int:
    """"6,240円" / "210" / "20分" → int。数字が無ければ ParseError."""
    if text is None:
        raise ParseError(field, "値が見つかりません（None）", snippet)
    digits = re.sub(r"[^\d]", "", text)
    if digits == "":
        raise ParseError(field, f"数字を抽出できません: {text!r}", snippet)
    return int(digits)


def _line_signature(line_names: list[str]) -> str:
    """路線名の並びを突き合わせ用キーに正規化.

    - 徒歩・空白は除外
    - 連続して同じ路線名が並ぶ場合は 1 つにまとめる
      （運賃ページは同一路線でも区間ごとに複数セグメントに割れることがあり、
       定期ページは 1 つにまとまる。両ページのキーを一致させるため畳む。
       例: 箱根登山電車|箱根登山電車 → 箱根登山電車）
    """
    norm: list[str] = []
    for ln in line_names:
        ln = re.sub(r"\s+", "", ln or "")
        if not ln or ln == "徒歩":
            continue
        if norm and norm[-1] == ln:  # 連続重複を畳む
            continue
        norm.append(ln)
    return "|".join(norm)


class EkitanScraper(TransitDataSource):
    """ekitan の運賃ページ・定期ページを解析する TransitDataSource."""

    name = "ekitan"

    def __init__(
        self,
        cache=None,
        request_delay_sec: float = 3.0,
        timeout_sec: int = 25,
        use_cache: bool = True,
        respect_robots: bool = True,
        robots_cache_dir: "str | None" = None,
    ):
        # cache は DiskCache でも DynamoDBCache でも可（get/set 互換ならよい）
        self.cache = cache or DiskCache()
        self.request_delay_sec = request_delay_sec  # リクエスト間スリープ（>=2~3秒）
        self.timeout_sec = timeout_sec
        self.use_cache = use_cache
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": _UA, "Accept-Language": "ja,en;q=0.8"}
        )
        # robots.txt はディスクにキャッシュする。Lambda 等で配置先が読み取り専用の
        # 場合は robots_cache_dir に書き込み可能な場所（/tmp 等）を渡す。
        if respect_robots:
            kwargs = {"cache_dir": robots_cache_dir} if robots_cache_dir else {}
            self.robots = RobotsGate(_BASE, _UA, self.session, **kwargs)
        else:
            self.robots = None

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
        key = f"{from_id}_{to_id}"

        bundle = None
        from_cache = False
        if self.use_cache:
            cached = self.cache.get(_CACHE_NS, key)
            if cached is not None:
                bundle = cached
                from_cache = True

        if bundle is None:
            bundle = self._fetch_and_parse(from_id, to_id)
            self.cache.set(_CACHE_NS, key, bundle)

        candidate = self._select(bundle["candidates"], strat)
        if candidate is None:
            raise RouteNotFoundError(
                f"{from_id}->{to_id}: 戦略 '{strat.value}' で選べる経路がありません"
                f"（候補 {len(bundle['candidates'])} 件、必須項目欠落の可能性）"
            )

        return CommuteResult(
            from_station=bundle["from_station"],
            to_station=bundle["to_station"],
            duration_min=candidate["duration_min"],
            transfers=candidate["transfers"],
            fare_ic_yen=candidate["fare_ic_yen"],
            fare_ticket_yen=candidate["fare_ticket_yen"],
            pass_1month_yen=candidate["pass_1month_yen"],
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
    # 取得 + 解析 + 統合（キャッシュ未命中時のみ）
    # ------------------------------------------------------------------ #
    def _fetch_and_parse(self, from_id: str, to_id: str) -> dict:
        fare_url = f"{_BASE}/transit/fare/sf-{from_id}/st-{to_id}"
        pass_url = f"{_BASE}/transit/pass/sf-{from_id}/st-{to_id}"

        fare_html = self._get(fare_url)
        # ★責任あるクロール: リクエスト間に必ずスリープ（直列・並列にしない）
        time.sleep(self.request_delay_sec)
        pass_html = self._get(pass_url)

        fare_candidates, from_station, to_station = self._parse_fare(fare_html)
        pass_candidates = self._parse_pass(pass_html)
        candidates = self._merge(fare_candidates, pass_candidates)

        if not candidates:
            raise RouteNotFoundError(
                f"{from_id}->{to_id}: 候補経路を 1 件も抽出できませんでした"
            )

        return {
            "queried_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "from_station": from_station,
            "to_station": to_station,
            "candidates": candidates,
        }

    def _get(self, url: str) -> str:
        if self.robots is not None and not self.robots.can_fetch(url):
            raise TransitDataError(f"robots.txt により取得不可: {url}")
        resp = self.session.get(url, timeout=self.timeout_sec)
        if resp.status_code != 200:
            raise TransitDataError(
                f"HTTP {resp.status_code} 取得失敗: {url}"
            )
        return resp.text

    # ------------------------------------------------------------------ #
    # 運賃ページ解析
    # ------------------------------------------------------------------ #
    def _parse_fare(self, html: str) -> tuple[list[dict], str, str]:
        soup = BeautifulSoup(html, "html.parser")
        blocks = soup.find_all(attrs={"data-ek-route-json": True})
        if not blocks:
            raise ParseError(
                "fare.routes",
                "運賃ページに data-ek-route-json を持つ経路ブロックが 1 つも無い"
                "（ページ改版 or JS 描画化の可能性）",
                snippet=html[:1500],
            )

        candidates: list[dict] = []
        from_station = to_station = ""
        for b in blocks:
            try:
                j = json.loads(b["data-ek-route-json"])
            except json.JSONDecodeError as e:
                raise ParseError(
                    "fare.route_json",
                    f"data-ek-route-json の JSON 解析失敗: {e}",
                    snippet=b.get("data-ek-route-json", "")[:800],
                )

            from_station = j.get("from", from_station)
            to_station = j.get("to", to_station)

            tm = j.get("time", {})
            duration_min = _clean_int(tm.get("hour"), "fare.time.hour") * 60 + \
                _clean_int(tm.get("min"), "fare.time.min")
            transfers = int(str(j.get("transfer", "0")) or "0")

            distance_km = None
            try:
                if j.get("distance") not in (None, ""):
                    distance_km = float(j["distance"])
            except (TypeError, ValueError):
                distance_km = None

            segs = (j.get("lineList") or {}).get("line") or []
            line_names = [s.get("lineName", "") for s in segs]
            route_summary = self._route_summary(segs)

            # 運賃（大人・片道）: きっぷ=ek-ticket_total / IC=ek-ic_total
            ticket = b.find("span", class_="ek-ticket_total")
            ic = b.find("span", class_="ek-ic_total")
            fare_ticket = _clean_int(
                ticket.get_text(strip=True) if ticket else None,
                "fare.ticket_total",
                snippet=b.prettify()[:800],
            )
            fare_ic = _clean_int(
                ic.get_text(strip=True) if ic else None,
                "fare.ic_total",
                snippet=b.prettify()[:800],
            )

            candidates.append(
                {
                    "line_sig": _line_signature(line_names),
                    "duration_min": duration_min,
                    "transfers": transfers,
                    "distance_km": distance_km,
                    "route_summary": route_summary,
                    "fare_ic_yen": fare_ic,
                    "fare_ticket_yen": fare_ticket,
                    "pass_1month_yen": None,
                    "pass_3month_yen": None,
                    "pass_6month_yen": None,
                }
            )
        return candidates, from_station, to_station

    @staticmethod
    def _route_summary(segs: list[dict]) -> str:
        """lineList から「駅 →[路線]→ 駅 …」を組み立てる.

        各区間に路線名を添え、駅間の徒歩連絡は〔徒歩〕で明示する。
        これにより「乗換回数」と「経由駅数」が食い違う理由（=徒歩連絡は
        乗換にカウントされないが駅名は変わる）が一目で分かる。
        例: 西大島→[都営新宿線]→神保町→[都営三田線]→春日〔徒歩〕後楽園→[南北線]→赤羽岩淵〔徒歩〕赤羽
        """
        out: list[str] = []
        for i, s in enumerate(segs):
            sf = (s.get("stationFrom") or {}).get("stationName") or ""
            st = (s.get("stationTo") or {}).get("stationName") or ""
            line = re.sub(r"\s+", "", s.get("lineName") or "")
            if i == 0 and sf:
                out.append(sf)
            if not st:
                continue
            if "徒歩" in line:
                out.append("〔徒歩〕")          # 駅間の徒歩連絡（乗換にはカウントされない）
            elif line:
                out.append("→[%s]→" % line)    # 乗車区間（路線名つき）
            else:
                out.append("→")
            out.append(st)
        return "".join(out)

    # ------------------------------------------------------------------ #
    # 定期ページ解析
    # ------------------------------------------------------------------ #
    def _parse_pass(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")

        # 上部サマリ（rank 順）: 路線パス・所要時間・乗換
        summaries = soup.find_all("tr", class_="ek-summary")
        # 詳細（通勤=business のみ）: 1/3/6ヶ月。rank 順（経路ごと）に並ぶ。
        # 注意: data-ek-display は business / college / highSchool /
        # juniorHighSchool / businessOffPeak。通学(college等)・オフピーク
        # (businessOffPeak)を拾わないよう「完全一致」で business だけを取る。
        # ("business" in "businessOffPeak" が真になる部分一致は不可）。
        business_wraps = [
            w
            for w in soup.find_all(class_="result-route-commutation-wrap")
            if (w.get("data-ek-display") or "") == "business"
        ]
        if not summaries and not business_wraps:
            raise ParseError(
                "pass.routes",
                "定期ページに ek-summary も business の定期ブロックも無い",
                snippet=html[:1500],
            )

        candidates: list[dict] = []
        # サマリと business 詳細を rank 順で対応付け（件数が食い違っても短い方に合わせる）
        for summary, wrap in zip(summaries, business_wraps):
            path_el = summary.find(class_="path")
            tt_el = summary.find(class_="total-time")
            tr_el = summary.find(class_="transfers")

            line_sig = self._pass_line_sig(
                path_el.get_text(strip=True) if path_el else ""
            )
            duration_min = (
                _clean_int(tt_el.get_text(strip=True), "pass.total_time")
                if tt_el
                else None
            )
            transfers = (
                _clean_int(tr_el.get_text(strip=True), "pass.transfers")
                if tr_el
                else None
            )

            months = self._parse_business_months(wrap)
            candidates.append(
                {
                    "line_sig": line_sig,
                    "duration_min": duration_min,
                    "transfers": transfers,
                    "pass_1month_yen": months.get(1),
                    "pass_3month_yen": months.get(3),
                    "pass_6month_yen": months.get(6),
                }
            )
        return candidates

    @staticmethod
    def _pass_line_sig(path_text: str) -> str:
        """定期サマリの路線パス（"ＪＲ総武線, ＪＲ山手線(外回り)"）を line_sig へ."""
        parts = re.split(r"[,、]", path_text)
        return _line_signature([p.strip() for p in parts])

    @staticmethod
    def _parse_business_months(wrap) -> dict[int, int]:
        """通勤定期ブロックから {1: 円, 3: 円, 6: 円} を取り出す."""
        result: dict[int, int] = {}
        if wrap is None:
            return result
        for col in wrap.find_all(class_="a-month"):
            span = col.find(class_="span")
            charge = col.find(class_="charge")
            if not span or not charge:
                continue
            m = re.search(r"(\d+)", span.get_text())
            if not m:
                continue
            months = int(m.group(1))
            try:
                result[months] = _clean_int(
                    charge.get_text(strip=True), "pass.charge"
                )
            except ParseError:
                continue
        return result

    # ------------------------------------------------------------------ #
    # 統合: 運賃候補に定期候補をマージ
    # ------------------------------------------------------------------ #
    def _merge(self, fare: list[dict], pass_: list[dict]) -> list[dict]:
        """運賃候補に定期代をマージする.

        対応付けは「路線シグネチャ（正規化済み）」をキーにする。
        所要時間は両ページで一致しない（運賃ページは乗車時間、定期ページは
        待ち時間込みの総時間で、長距離・乗換多の経路ほど大きくずれる）ため
        キーに使わない。同一シグネチャの経路が複数ある場合は、両ページとも
        rank 順に並ぶ前提で「出てきた順」に突き合わせる。
        """
        from collections import defaultdict, deque

        def attach(f: dict, p: dict) -> None:
            f["pass_1month_yen"] = p.get("pass_1month_yen")
            f["pass_3month_yen"] = p.get("pass_3month_yen")
            f["pass_6month_yen"] = p.get("pass_6month_yen")

        queues: dict[str, deque] = defaultdict(deque)
        for p in pass_:
            queues[p["line_sig"]].append(p)

        matched = 0
        for f in fare:
            q = queues.get(f["line_sig"])
            if q:
                attach(f, q.popleft())
                matched += 1

        # シグネチャが 1 件も一致しないとき（路線名表記の差異など）は、
        # 両ページの rank 順インデックスで素朴に対応付ける退避策。
        if matched == 0 and fare and pass_:
            print(
                "[警告] 路線シグネチャで定期代を対応付けできず、rank 順で代替対応します。",
                file=sys.stderr,
            )
            for f, p in zip(fare, pass_):
                attach(f, p)
        return fare

    # ------------------------------------------------------------------ #
    # 戦略による選択
    # ------------------------------------------------------------------ #
    @staticmethod
    def _select(candidates: list[dict], strat: Strategy) -> Optional[dict]:
        # 4 つの必須項目が揃っている候補だけを対象にする
        usable = [
            c
            for c in candidates
            if c.get("fare_ic_yen") is not None
            and c.get("fare_ticket_yen") is not None
            and c.get("pass_1month_yen") is not None
            and c.get("duration_min") is not None
        ]
        if not usable:
            return None

        if strat is Strategy.CHEAPEST:
            # 定期代 1ヶ月が最安、同額なら所要時間が短い方
            return min(
                usable, key=lambda c: (c["pass_1month_yen"], c["duration_min"])
            )
        # FASTEST: 所要時間が最短、同時間なら定期代が安い方
        return min(
            usable, key=lambda c: (c["duration_min"], c["pass_1month_yen"])
        )
