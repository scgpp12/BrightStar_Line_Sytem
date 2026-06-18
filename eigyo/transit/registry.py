"""駅マスタ（駅名 <-> 各データソースの駅ID 対照表）.

データソースごとに駅IDの体系が違う（ekitan は内部数字ID、駅すぱあとは別コード）。
そこで stations.json で「駅名 → 各ソースの駅ID」を持ち、入力の入口にする。

stations.json の値は 2 形式を許容（後方互換）:
  1. 文字列            "2590"                       … ekitan の駅IDとして扱う
  2. オブジェクト      {"ekitan": "2590", "ekispert": "22828"}  … ソース別に保持

上位層は駅名でも駅IDでも渡せる:
  - 数字ID（"2590"）や "sf-2590"/"st-2590" 形式 → そのままIDとして解決
  - 駅名（"東京"）→ 対照表でアクティブなソースの駅IDに変換

新しい駅IDの拾い方は README 参照（resolve_station.py / harvest_tokyo_stations.py）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .exceptions import StationNotFoundError

# "sf-2927" / "st-2590" / "2927" のいずれからも数字IDを取り出す（ekitan 用）
_ID_PATTERN = re.compile(r"(?:s[ft]-)?(\d+)$", re.IGNORECASE)

DEFAULT_SOURCE = "ekitan"


class StationRegistry:
    """stations.json を読み、駅名/ID表記をソース別の駅IDへ解決する."""

    def __init__(self, mapping: dict[str, str | dict[str, str]]):
        # name -> {source: id}
        self._name_to_ids: dict[str, dict[str, str]] = {}
        # ekitan の id -> name（表示補助）
        self._ekitan_id_to_name: dict[str, str] = {}

        for name, value in mapping.items():
            ids = self._parse_value(value)
            self._name_to_ids[name] = ids
            if "ekitan" in ids:
                self._ekitan_id_to_name.setdefault(ids["ekitan"], name)

    @staticmethod
    def _parse_value(value: "str | dict[str, str]") -> dict[str, str]:
        """stations.json の 1 エントリ値を {source: id} に正規化する."""
        if isinstance(value, str):
            # 旧形式（文字列）= ekitan の駅ID
            return {"ekitan": StationRegistry._normalize_ekitan(value)}
        if isinstance(value, dict):
            ids = dict(value)
            if "ekitan" in ids:
                ids["ekitan"] = StationRegistry._normalize_ekitan(str(ids["ekitan"]))
            return ids
        raise StationNotFoundError(f"駅マスタの値が不正です: {value!r}")

    @classmethod
    def from_file(cls, path: "str | Path") -> "StationRegistry":
        p = Path(path)
        if not p.exists():
            raise StationNotFoundError(f"駅マスタが見つかりません: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise StationNotFoundError(
                f"駅マスタの形式が不正です（オブジェクトを期待）: {p}"
            )
        return cls(data)

    @staticmethod
    def _normalize_ekitan(value: str) -> str:
        """"sf-2927" / "st-2590" / "2927" → "2927"."""
        m = _ID_PATTERN.search(value.strip())
        if not m:
            raise StationNotFoundError(f"ekitan 駅IDを解釈できません: {value!r}")
        return m.group(1)

    def resolve(self, station: str, source: str = DEFAULT_SOURCE) -> str:
        """駅名または駅ID表記を、指定ソースの駅IDに解決する.

        - ekitan: 数字/`sf-`/`st-` はそのままID、駅名は対照表で変換。
          未知の駅名はエラー（master を整備済みのため）。
        - その他（ekispert 等）: 対照表に当該ソースのコードがあれば返す。
          無ければ **入力文字列をそのまま返す**（コード直指定 or
          ソース側で駅名解決させる前提。これで未整備でも動く）。
        """
        station = station.strip()

        if source == "ekitan":
            if _ID_PATTERN.fullmatch(station):
                return self._normalize_ekitan(station)
            ids = self._name_to_ids.get(station)
            if ids and ids.get("ekitan"):
                return ids["ekitan"]
            raise StationNotFoundError(
                f"駅 '{station}' は対照表(ekitan)にありません。"
                f"`python resolve_station.py {station}` でIDを調べて stations.json に追加してください。"
            )

        # 非 ekitan ソース
        ids = self._name_to_ids.get(station)
        if ids and ids.get(source):
            return ids[source]
        # 当該ソースのコード未整備 → 入力をそのまま返す（ソース側で解決）
        return station

    def name_of(self, station_id: str, source: str = DEFAULT_SOURCE) -> str | None:
        """駅ID → 駅名（無ければ None）。表示の補助用（現状 ekitan のみ）。"""
        if source == "ekitan":
            return self._ekitan_id_to_name.get(self._normalize_ekitan(station_id))
        for name, ids in self._name_to_ids.items():
            if ids.get(source) == station_id:
                return name
        return None
