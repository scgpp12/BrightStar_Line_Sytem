"""データソース抽象基底（上位層が依存する唯一の契約）.

★ 差し替え契約 ★
  上位層（commute.py / 駅マスタ）は、この TransitDataSource インタフェースと
  CommuteResult にだけ依存する。具象実装（EkitanScraper など）には依存しない。

  将来 EkispertApiSource（駅すぱあと API）を足すときは:
    1. TransitDataSource を継承
    2. query(from_id, to_id, strategy) を実装
    3. 同じ CommuteResult を返す
  これだけで済む。main も stations.json の読み込み層も 1 行も変えない。

  そのため、このインタフェースには ekitan 固有の概念
  （HTML・候補リスト構造・sf-/st- などの URL 形式）を一切載せない。
  - from_id / to_id は「そのデータソースが理解する駅ID」を表す不透明な文字列。
    ekitan なら "2927"（EkitanScraper が内部で sf-/st- を付ける）、
    駅すぱあとなら駅すぱあとのコード。対照表(stations.json)がソース別に ID を持つ。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import CommuteResult, Strategy


class TransitDataSource(ABC):
    """通勤コスト調査データソースの共通インタフェース."""

    #: ソース識別子（CommuteResult.source に入る）。サブクラスで上書きする。
    name: str = "abstract"

    @abstractmethod
    def query(
        self,
        from_id: str,
        to_id: str,
        strategy: "str | Strategy" = Strategy.CHEAPEST,
    ) -> CommuteResult:
        """出発駅IDと到着駅IDから 1 経路ぶんの通勤コストを返す.

        Args:
            from_id: 出発駅ID（そのデータソースが理解する形式の不透明文字列）。
            to_id:   到着駅ID（同上）。
            strategy: 複数候補からの選び方（cheapest / fastest）。

        Returns:
            CommuteResult（戦略に基づき選ばれた 1 経路）。

        Raises:
            RouteNotFoundError: 候補が無い／戦略で選べない。
            ParseError:         ページ等から想定フィールドを取れない。
            TransitDataError:   その他のデータ取得失敗。
        """
        raise NotImplementedError
