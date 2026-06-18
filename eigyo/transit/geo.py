"""住所 → 最寄駅 解決（Amazon Location Service / geo-places）.

社員の登録情報が「駅名」ではなく実住所のことがある。その場合:
  1. geocode で住所 → 緯度経度
  2. その地点の近くの「駅」を検索（鉄道/地下鉄カテゴリのみ）
  3. 最も近い駅名を取り出して整形（「木場駅」→「木場」、「門前仲町駅 (大江戸線)」→「門前仲町」）

★個人情報配慮★ 解決後は **駅名だけ** を保存し、住所は保存しない方針
（このモジュールは登録時に駅名へ変換するためのもの）。

Amazon Location の新しい Places API（client 'geo-places'。アカウントレベル・リソース不要）を使う。
Lambda では IAM に geo-places:Geocode / geo-places:SearchText が必要。
"""

from __future__ import annotations

import os
import re
from typing import Optional

import boto3

# 駅とみなすカテゴリ（Id に train / subway を含むもの）
_STATION_HINT = ("train", "subway")


def _clean_station_name(title: str) -> str:
    """検索結果のタイトルを駅名に整形."""
    # 末尾の路線注記 "(大江戸線)" 等を除去
    name = re.sub(r"\s*[（(][^（）()]*[）)]\s*$", "", title)
    # 末尾の「駅」を除去
    name = re.sub(r"駅$", "", name).strip()
    return name


def nearest_station(address: str, region: Optional[str] = None) -> dict:
    """住所から最寄駅を返す.

    Returns: {"station": 駅名, "distance_m": int, "address_label": 正規化住所, "raw": 元タイトル}
    Raises: ValueError（住所をジオコーディングできない / 近くに駅が無い）
    """
    region = region or os.environ.get("AWS_REGION", "ap-northeast-1")
    gp = boto3.client("geo-places", region_name=region)

    geo = gp.geocode(QueryText=address, MaxResults=1)
    items = geo.get("ResultItems", [])
    if not items:
        raise ValueError(f"住所をジオコーディングできませんでした: {address}")
    pos = items[0]["Position"]  # [lng, lat]
    label = items[0].get("Address", {}).get("Label", address)

    res = gp.search_text(QueryText="駅", BiasPosition=pos, MaxResults=15)
    stations = []
    for x in res.get("ResultItems", []):
        cat_ids = [c.get("Id", "") for c in x.get("Categories", [])]
        if any(h in cid for cid in cat_ids for h in _STATION_HINT):
            stations.append(x)
    if not stations:
        raise ValueError(f"近くに駅が見つかりませんでした: {address}")

    stations.sort(key=lambda x: x.get("Distance", 10**9))
    best = stations[0]
    return {
        "station": _clean_station_name(best.get("Title", "")),
        "distance_m": best.get("Distance"),
        "address_label": label,
        "raw": best.get("Title"),
    }
