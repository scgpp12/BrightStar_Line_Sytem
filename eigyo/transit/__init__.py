"""通勤コスト調査 原型（プロトタイプ）パッケージ.

レイヤ構成（差し替え契約の要）:
  - models       : CommuteResult（データソース非依存の戻り値）と例外
  - data_source  : TransitDataSource 抽象基底（上位層が依存する唯一の契約）
  - registry     : 駅名 <-> 駅ID 対照表（stations.json）
  - ekitan_source: TransitDataSource の ekitan 実装（HTML 解析はここに閉じ込める）
  - cache / robots: 責任あるクローリング用ユーティリティ

上位層（commute.py）は TransitDataSource と CommuteResult にだけ依存する。
将来 EkispertApiSource（駅すぱあと API）を足すときは、同じ query() を実装し
同じ CommuteResult を返すだけでよい。
"""

from .models import CommuteResult, Strategy
from .data_source import TransitDataSource
from .exceptions import (
    TransitDataError,
    ParseError,
    StationNotFoundError,
    RouteNotFoundError,
)

__all__ = [
    "CommuteResult",
    "Strategy",
    "TransitDataSource",
    "TransitDataError",
    "ParseError",
    "StationNotFoundError",
    "RouteNotFoundError",
]
