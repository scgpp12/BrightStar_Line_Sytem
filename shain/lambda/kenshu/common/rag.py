"""自建 RAG（不依赖 OpenSearch）：

  Titan 向量化 → DynamoDB 暴力余弦检索 → Bedrock(Claude) 生成。

仅在学员开启「人工智能回复」后调用。知识块存在 KnowledgeTable，
量小（几十~几千条）直接全量扫描算余弦即可；语料变大再换 ANN/向量库。
向量已归一化，点积即余弦。生成默认走日本境内 inference profile，数据不出日本。
"""
import base64
import json
import logging
import struct

import boto3

from . import config, db
from .i18n import T

log = logging.getLogger()

_brt = None


def _client():
    global _brt
    if _brt is None:
        _brt = boto3.client("bedrock-runtime")
    return _brt


# ------------------------------- 向量 -------------------------------
def embed(text: str):
    """Titan v2 向量（归一化，维度 config.BEDROCK_EMBED_DIM）。"""
    body = {
        "inputText": (text or "")[:8000],
        "dimensions": config.BEDROCK_EMBED_DIM,
        "normalize": True,
    }
    r = _client().invoke_model(modelId=config.BEDROCK_EMBED_MODEL_ID, body=json.dumps(body))
    return json.loads(r["body"].read())["embedding"]


def pack_vec(vec) -> str:
    """float 列表 → base64(float32)，存 DynamoDB 紧凑省钱。"""
    return base64.b64encode(struct.pack(f"{len(vec)}f", *vec)).decode("ascii")


def unpack_vec(s: str):
    raw = base64.b64decode(s)
    return list(struct.unpack(f"{len(raw) // 4}f", raw))


def _cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


# ------------------------------- 检索 -------------------------------
def retrieve(question: str, k: int = 3, min_score: float = 0.2):
    """返回 (命中块列表, 最高分)。向量均归一化，点积=余弦。"""
    qv = embed(question)
    items = db.knowledge().scan().get("Items", [])
    scored = []
    for it in items:
        v = it.get("vec")
        if not v:
            continue
        try:
            scored.append((_cosine(qv, unpack_vec(v)), it))
        except Exception:  # noqa: BLE001
            continue
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [it for s, it in scored[:k] if s >= min_score]
    return top, (scored[0][0] if scored else 0.0)


# ------------------------------- 生成 -------------------------------
_SYS = {
    "zh": (
        "你是 BrightStar 培训助手的答疑机器人。只依据下面【资料】回答学员问题，"
        "简洁清楚、口语化。资料里没有的内容就如实说不清楚，并建议学员发「菜单」查看功能。"
        "务必用中文回答。"
    ),
    "ja": (
        "あなたは BrightStar 研修アシスタントのQ&Aボットです。以下の【資料】のみに基づき、"
        "簡潔で分かりやすく答えてください。資料に無い場合は分からない旨を伝え、"
        "「メニュー」と送るよう案内してください。必ず日本語で答えてください。"
    ),
}


def answer(question: str, lang: str = "ja") -> str:
    """RAG 问答：检索 + 生成。任何环节失败都回友好兜底，不抛异常。"""
    try:
        chunks, _best = retrieve(question)
    except Exception:  # noqa: BLE001
        log.exception("rag retrieve failed")
        return T(lang, "ai_error")

    ctx = "\n\n".join(
        f"[{c.get('title', '')}] {c.get('text', '')}" for c in chunks
    ) or ("（无相关资料）" if lang == "zh" else "（関連資料なし）")
    user = f"【资料】\n{ctx}\n\n【问题】{question}"

    try:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 500,
            "system": _SYS.get(lang, _SYS["ja"]),
            "messages": [{"role": "user", "content": user}],
        }
        r = _client().invoke_model(modelId=config.BEDROCK_CHAT_MODEL_ID, body=json.dumps(body))
        out = json.loads(r["body"].read())
        return out["content"][0]["text"].strip()
    except Exception:  # noqa: BLE001
        log.exception("rag generate failed")
        return T(lang, "ai_error")
