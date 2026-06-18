"""LINE 助手共通モジュール（多言語キーワード解決 / 言語設定 / Quick Reply ヘルプ）。

各 LINE 助手はこのファイルを vendoring。言語は authlib と同じ auth テーブル
(env AUTH_TABLE)の認証行に lang / langDate(JST) で保持する。
- 多言語キーワード：英/日/韓/中。意味が同じなら同じ canonical intent に解決。
- 言語設定：毎日初回認証後に「日本語/中文」を選ばせ、その日の返信言語にする（既定 ja）。
- ヘルプ：機能一覧テキスト + 各機能を Quick Reply ボタンで提示（タップで即実行）。
"""
import os
from datetime import datetime, timedelta, timezone

import boto3

JST = timezone(timedelta(hours=9))
DEFAULT_LANG = "ja"
LANG_LABELS = {"ja": "日本語", "zh": "中文"}

_ddb = None


def _auth():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))
    return _ddb.Table(os.environ["AUTH_TABLE"])


def _today():
    return datetime.now(JST).strftime("%Y-%m-%d")


def _key(channel, user_id):
    return {"pk": "%s#%s" % (channel, user_id)}


# ---------------- 言語設定（auth 行に保存）----------------
def get_lang(channel, user_id):
    try:
        it = _auth().get_item(Key=_key(channel, user_id)).get("Item") or {}
        return it.get("lang") or DEFAULT_LANG
    except Exception:  # noqa: BLE001
        return DEFAULT_LANG


def set_lang(channel, user_id, lang):
    lang = lang if lang in LANG_LABELS else DEFAULT_LANG
    try:
        _auth().update_item(
            Key=_key(channel, user_id),
            UpdateExpression="SET lang=:l, langDate=:d",
            ExpressionAttributeValues={":l": lang, ":d": _today()})
    except Exception:  # noqa: BLE001
        pass
    return lang


def needs_lang_today(channel, user_id):
    """今日まだ言語を選んでいない → True（毎日初回 認証後に聞く）。"""
    try:
        it = _auth().get_item(Key=_key(channel, user_id)).get("Item") or {}
        return it.get("langDate") != _today()
    except Exception:  # noqa: BLE001
        return True


# 言語選択ワード（多言語）
LANG_WORDS = {
    "ja": {"日本語", "にほんご", "japanese", "japan", "jp", "ja", "日语", "日文", "일본어", "일본語"},
    "zh": {"中文", "中国語", "中國語", "chinese", "zh", "cn", "簡体", "简体", "汉语", "漢語", "중국어"},
}


def detect_lang_word(text):
    t = (text or "").strip().lower()
    for lang, words in LANG_WORDS.items():
        if t in {w.lower() for w in words}:
            return lang
    return None


# ---------------- Quick Reply ----------------
def quick_reply(text, items):
    """items: [(label, send_text), ...]（最大13）。LINE の message dict を返す。"""
    qr = [{"type": "action", "action": {"type": "message", "label": lbl[:20], "text": msg}}
          for lbl, msg in items[:13]]
    msg = {"type": "text", "text": text}
    if qr:
        msg["quickReply"] = {"items": qr}
    return msg


def lang_chooser(name=""):
    head = ("✅ 認証OK：%s\n" % name) if name else ""
    return quick_reply(head + "言語を選んでください / 请选择语言（默认日语）",
                       [("日本語", "日本語"), ("中文", "中文")])


# ---------------- ヘルプ（機能一覧 + Quick Reply ボタン）----------------
def help_message(lang, title, entries):
    """entries: [(desc_ja, desc_zh, button_label or None, send_keyword or None), ...]"""
    lines = [title]
    items = []
    for ja, zh, label, kw in entries:
        lines.append("・" + (ja if lang == "ja" else zh))
        if label and kw:
            items.append((label, kw))
    return quick_reply("\n".join(lines), items)


# ---------------- 多言語キーワード解決 ----------------
def resolve(text, intents):
    """intents: {canonical: set(keywords)}。text と完全一致する canonical を返す（無ければ None）。"""
    t = (text or "").strip().lower()
    if not t:
        return None
    for canon, words in intents.items():
        if t in {w.lower() for w in words}:
            return canon
    return None


# 全助手共通の intent（ヘルプ / 認証 / 登録解除）。各助手の intents にマージして使う。
COMMON_INTENTS = {
    "help": {"ヘルプ", "助け", "使い方", "メニュー", "help", "menu", "?", "？",
             "帮助", "菜单", "幫助", "選單", "功能", "도움말", "메뉴"},
    "auth": {"認証", "本日認証", "出勤", "auth", "认证", "認證", "인증"},
    "reset": {"登録解除", "リセット", "解除", "登録変更", "重新登録", "重新登记", "리셋", "reset"},
}
