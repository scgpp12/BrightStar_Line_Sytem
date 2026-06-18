"""パッケージ共通の例外.

データソースに依存しない汎用例外だけを置く（ekitan 固有の語彙は持ち込まない）。
解析失敗時は「どこで・何を探していたか・拾えた生データ断片」を必ず添えること。
ページ改版時の原因切り分けが何より重要なため。
"""


class TransitDataError(Exception):
    """このパッケージが投げる例外の基底クラス."""


class StationNotFoundError(TransitDataError):
    """駅名・駅IDが対照表（stations.json）で解決できない."""


class ParseError(TransitDataError):
    """ページから期待したフィールドを取り出せなかった.

    どの項目を探していたか（field）と、拾えた生 HTML 断片（snippet）を保持する。
    ページ改版でレイアウトが変わったときの一次切り分け材料になる。
    """

    def __init__(self, field: str, message: str, snippet: str | None = None):
        self.field = field
        self.snippet = snippet
        full = f"[{field}] {message}"
        if snippet:
            # 生断片は長すぎると読みづらいので頭 800 文字だけ
            full += f"\n--- 取得できた生データ断片 ---\n{snippet[:800]}"
        super().__init__(full)


class RouteNotFoundError(TransitDataError):
    """候補経路が 0 件、または指定戦略で選べる経路が無かった."""
