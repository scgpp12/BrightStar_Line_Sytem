"""現場/駅名のあいまい解決（かな・ローマ字・日本語駅名 → ekitan 駅ID）.

LINE では現場をいろいろな表記で送ってくる。段階的に解決する:
  1. 対照表で完全一致 / 数字ID
  2. ekitan サジェスト API（かな・ローマ字・部分一致・日本語駅名に強い。首都圏 area=0 優先）
  3. （任意）中国語など 1・2 で解けない表記は Bedrock で日本語駅名へ正規化 → 再度サジェスト
     ※ Bedrock は環境変数 USE_BEDROCK_NORMALIZE=1 のときだけ・最後の手段として呼ぶ（普段は不要）。

実測（2026-06）: 平仮名/片仮名/ローマ字/日本語駅名はサジェストで解決可。簡体字中国語のみ不可。
"""

from __future__ import annotations

import json
import os

from .exceptions import StationNotFoundError
from .registry import StationRegistry
from .station_lookup import suggest, AREA_TOKYO


def _pick_from_suggest(query: str) -> str | None:
    try:
        results = suggest(query)
    except Exception:  # noqa: BLE001 - サジェスト不達でも解決失敗として扱うだけ
        return None
    tokyo = [s for s in results if s.get("area") == AREA_TOKYO and s.get("code")]
    pool = tokyo or [s for s in results if s.get("code")]
    return pool[0]["code"] if pool else None


def resolve_ekitan_id(
    query: str,
    registry: StationRegistry,
    allow_suggest: bool = True,
) -> str:
    """駅名/ID表記（かな・ローマ字・日本語可）を ekitan 駅IDに解決する."""
    q = query.strip()

    # 1) 完全一致 / 数字ID
    try:
        return registry.resolve(q, "ekitan")
    except StationNotFoundError:
        pass

    # 2) サジェスト API
    if allow_suggest:
        code = _pick_from_suggest(q)
        if code:
            return code

        # 3) 中国語など → Bedrock 正規化（任意・最後の手段）
        if os.environ.get("USE_BEDROCK_NORMALIZE") == "1":
            normalized = _bedrock_normalize(q)
            if normalized and normalized != q:
                code = _pick_from_suggest(normalized)
                if code:
                    return code

    raise StationNotFoundError(
        f"駅 '{query}' を解決できませんでした。"
        "かな・ローマ字・日本語の駅名で送ってください"
        "（中国語表記はそのままでは未対応）。"
    )


def _bedrock_normalize(query: str) -> str | None:
    """任意表記（中国語等）を日本語の駅名に正規化する（Bedrock）.

    モデルは環境変数 BEDROCK_MODEL_ID（既定: 軽量な Claude Haiku）。
    Bedrock のモデルアクセス有効化が前提。失敗時は None（兜底なので落とさない）。
    """
    try:
        import re

        import boto3

        # Claude はオンデマンド不可・推論プロファイル経由。東京は jp. プロファイル。
        model_id = os.environ.get(
            "BEDROCK_MODEL_ID", "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
        )
        client = boto3.client("bedrock-runtime")
        prompt = (
            "次の入力は日本の鉄道駅名を指している可能性があります（中国語表記の場合あり）。"
            "対応する日本語の正式な駅名だけを1つ、「駅」や余計な語を付けずに出力してください。"
            "鉄道駅と判断できない場合は INPUT をそのまま返してください。\n"
            f"INPUT: {query}"
        )
        resp = client.invoke_model(
            modelId=model_id,
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 30,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ),
        )
        data = json.loads(resp["body"].read())
        text = data["content"][0]["text"].strip().strip("「」\"' 　")
        # 念のため末尾の「駅」を除去（サジェストは "渋谷駅" を部分一致できない）
        return re.sub(r"駅$", "", text) or None
    except Exception:  # noqa: BLE001
        return None
