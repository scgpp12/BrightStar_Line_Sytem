"""AI 对话层：用东京区 Amazon Bedrock 把自由文字解析成 {intent, params}。

设计要点（技术文档第 7 节）：
- 仅用于「自由打字」；菜单点击不经过 LLM。
- 低置信度 / 无法解析 / 缺参数 → 不臆测，返回 fallback，由上层回退菜单或反问一次。
- 合规：提示中不传 PII（仅传用户这句话本身）；模型在东京区，数据不出境。
- BEDROCK_ENABLED=false 或调用失败时，自动退化为关键词解析，保证可用。
"""
import json
import logging

from . import config

log = logging.getLogger()

# 支持的意图（与业务函数一一对应）
INTENTS = [
    "list_courses",   # 浏览课程
    "enroll",         # 报名（params.course = 课程关键词/标题）
    "cancel",         # 取消报名（params.course）
    "my_courses",     # 我的课程
    "next_class",     # 查下节课
    "register",       # 我要加入/注册
    "help",           # 帮助/菜单
]

_SYSTEM = (
    "你是培训助手的意图解析器。把用户中文消息解析为 JSON："
    '{"intent": <意图>, "params": {"course": <课程关键词或空>}, "confidence": <0~1>}。'
    f"intent 只能是其中之一：{INTENTS}。"
    "只输出 JSON，不要任何多余文字。无法判断时 intent 用 help、confidence 给低值。"
)

# 关键词兜底表（中 + 日）
_KEYWORDS = [
    ("next_class", ["下节课", "下一节", "几点", "什么时候上", "zoom", "链接", "会议链接",
                    "次の講座", "次の授業", "次回", "何時", "リンク"]),
    ("my_courses", ["我的课", "我报了", "报了哪些", "我的课程",
                    "マイ講座", "私の講座", "申込済", "受講中"]),
    ("cancel", ["取消", "退课", "退报名", "不上了",
                "キャンセル", "取消し", "やめ", "解約"]),
    ("enroll", ["报名", "我要报", "参加", "加入这门", "选课",
                "申込", "申し込み", "申込み", "受講", "登録したい講座"]),
    ("list_courses", ["有哪些课", "课程列表", "看课", "浏览", "有什么课",
                      "講座一覧", "講座", "授業一覧", "コース一覧", "どんな講座"]),
    ("register", ["我要加入", "注册", "加入", "怎么开始",
                  "参加したい", "始め方", "登録"]),
    ("help", ["帮助", "菜单", "怎么用", "你好", "在吗",
              "ヘルプ", "メニュー", "使い方", "こんにちは"]),
]

CONFIDENCE_THRESHOLD = 0.5


def _keyword_parse(text: str) -> dict:
    for intent, kws in _KEYWORDS:
        for kw in kws:
            if kw in text:
                course = _extract_course(text, kw) if intent in ("enroll", "cancel") else ""
                return {"intent": intent, "params": {"course": course}, "confidence": 0.6}
    return {"intent": "help", "params": {}, "confidence": 0.3}


def _extract_course(text: str, kw: str) -> str:
    """抽取课程关键词：只去掉【开头的动作词】，其余原样保留（课程名可能含「课/程」）。"""
    t = text.strip()
    for prefix in (
        "我要报名", "我要报", "报名", "参加", "选课",
        "取消报名", "退报名", "取消", "退课", "不上了",
        "申し込み", "申込み", "申込", "受講", "キャンセル", "取消し", "解約",
    ):
        if t.startswith(prefix):
            t = t[len(prefix):]
            break
    return t.strip(" :：のを　")


def _bedrock_parse(text: str) -> dict:
    import boto3

    client = boto3.client("bedrock-runtime")
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 200,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": text}],
    }
    resp = client.invoke_model(
        modelId=config.BEDROCK_MODEL_ID, body=json.dumps(body)
    )
    payload = json.loads(resp["body"].read())
    out = payload["content"][0]["text"].strip()
    # 容错：截取第一个 { 到最后一个 }
    start, end = out.find("{"), out.rfind("}")
    parsed = json.loads(out[start : end + 1])
    intent = parsed.get("intent", "help")
    if intent not in INTENTS:
        intent = "help"
    return {
        "intent": intent,
        "params": parsed.get("params", {}) or {},
        "confidence": float(parsed.get("confidence", 0.5)),
    }


def parse_intent(text: str) -> dict:
    """返回 {intent, params, confidence}。失败/低分由上层兜底。"""
    if not text:
        return {"intent": "help", "params": {}, "confidence": 0.0}
    if not config.BEDROCK_ENABLED:
        return _keyword_parse(text)
    try:
        result = _bedrock_parse(text)
        if result["confidence"] < CONFIDENCE_THRESHOLD:
            # 低置信度时再叠加关键词兜底，取更确定的
            kw = _keyword_parse(text)
            if kw["confidence"] >= result["confidence"]:
                return kw
        return result
    except Exception:  # noqa: BLE001
        log.exception("bedrock parse failed, fallback to keywords")
        return _keyword_parse(text)
