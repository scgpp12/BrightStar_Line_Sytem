"""ekitan の駅サジェスト API で「駅名 → 駅ID」を解決する.

ekitan の検索ボックスが叩く内部 API（suggest-common-ver5.js より逆引き）:
    GET https://mob-gw.ekitan.com/inc/v2/n_station?q=<駅名>&c=
    -> [{"more":bool,"result":[{"code","name","ruby","area","company"}, ...]}]

- code   = そのまま ekitan の駅ID（stations.json に入れる値）
- name   = 駅名（同名駅は "平井(東京)" のように括弧付き）
- area   = エリアコード。0 = 首都圏（=「東京周辺」の絞り込みに使える）
- company= 所属路線/事業者コード（カンマ区切り。さらなる消歧用）
- lkey は不要（付けても無視される）

※ これは公開 API ではなく検索ボックス用の内部 API。stations.json 整備のための
   一時的・少量利用に留め、責任ある利用（直列・キャッシュ・低頻度）を守ること。
   正式版では駅すぱあと/駅探法人 API の公式駅マスタに置き換える前提。
"""

from __future__ import annotations

import requests

_SUGGEST_URL = "https://mob-gw.ekitan.com/inc/v2/n_station"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 "
    "(commute-cost-prototype; internal tool)"
)

# エリアコード（観測値・主要なものだけ。表示補助用）
AREA_LABELS = {
    "0": "首都圏",
    "2": "東海",
    "4": "東北",
    "6": "甲信越・北陸",
    "7": "中国",
    "8": "四国・中国",
    "9": "九州",
}

#: 首都圏（東京周辺）のエリアコード
AREA_TOKYO = "0"


def suggest(word: str, session: requests.Session | None = None) -> list[dict]:
    """駅名（部分可）でサジェストし、候補のリストを返す.

    返り値は [{code, name, ruby, area, company}, ...]。前方/部分一致なので
    複数返る。呼び出し側で name 完全一致や area で絞ること。
    """
    sess = session or requests.Session()
    resp = sess.get(
        _SUGGEST_URL,
        params={"q": word, "c": ""},
        headers={"User-Agent": _UA, "Referer": "https://ekitan.com/"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data or "result" not in data[0]:
        return []
    return data[0]["result"]


def resolve_tokyo(word: str, session: requests.Session | None = None) -> dict | None:
    """首都圏(area=0)で駅名が完全一致する候補を 1 件返す（無ければ None）.

    同名が首都圏に複数ある場合も None を返す（曖昧なので自動採用しない）。
    """
    cands = [
        s
        for s in suggest(word, session)
        if s.get("area") == AREA_TOKYO and s.get("name") == word
    ]
    # 完全一致が無ければ「首都圏で name が word で始まる」唯一候補にフォールバック
    if not cands:
        starts = [
            s
            for s in suggest(word, session)
            if s.get("area") == AREA_TOKYO and s.get("name", "").startswith(word)
        ]
        cands = starts
    return cands[0] if len(cands) == 1 else None
