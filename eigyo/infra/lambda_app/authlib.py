"""全社花名册ベースの「部门 姓名」日次認証（4 channel 共有・自己完結モジュール）。

身份源 = roster テーブル（env ROSTER_TABLE）：empId/name/department/role/lineUserId。
認証状態 = auth テーブル（env AUTH_TABLE, PK 'pk' = '<channel>#<userId>'）に authedDate(JST) を保存。
日次：authedDate != 今日(JST) なら再認証が必要。

依存は boto3 + 標準ライブラリのみ。各 channel はこのファイルをそのまま vendoring して使う。
利用側は authenticate(channel, user_id, text, role_pred) を呼び、status で分岐する。
"""
import os
import re
from datetime import datetime, timedelta, timezone

import boto3

JST = timezone(timedelta(hours=9))
_ddb = None


def _res():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))
    return _ddb


def _roster():
    return _res().Table(os.environ["ROSTER_TABLE"])


def _auth():
    return _res().Table(os.environ["AUTH_TABLE"])


def today_jst():
    return datetime.now(JST).strftime("%Y-%m-%d")


def _expire_epoch():
    """auth 行の TTL（2日後）。DynamoDB が自動削除する。"""
    return int(datetime.now(JST).timestamp()) + 2 * 86400


# 「本日認証」ワンタップ用キーワード（Rich Menu のボタンも同文言を送る）
TAP_WORDS = {"認証", "认证", "本日認証", "本人確認", "本日認証する", "はい", "ok", "OK", "確認", "出勤"}

# 「登録解除 / 別人で登録し直す」キーワード（紐付けを解除して再登録できる）
RESET_WORDS = {"登録解除", "登録変更", "別アカウント", "リセット", "解除",
               "重新登録", "重新注册", "重新登记", "切换身份", "切換身分", "登録し直す"}

CHANNELS = ("jinji", "kenshu", "eigyo", "shain")


def unbind(user_id):
    """この LINE アカウントの紐付けを解除する：
    roster.lineUserId を**全エントリ**でクリア + 全 channel の認証行を削除。戻り値＝氏名 or None。"""
    name = None
    for r in _scan_roster():
        if r.get("lineUserId") == user_id:
            name = name or r.get("name")
            try:
                _roster().update_item(Key={"empId": r["empId"]}, UpdateExpression="REMOVE lineUserId")
            except Exception:  # noqa: BLE001
                pass
    for ch in CHANNELS:
        try:
            _auth().delete_item(Key={"pk": "%s#%s" % (ch, user_id)})
        except Exception:  # noqa: BLE001
            pass
    return name


def _norm(s):
    return re.sub(r"[\s　]+", "", (s or "")).strip()


def _scan_roster():
    items, kw = [], {}
    while True:
        r = _roster().scan(**kw)
        items += r.get("Items", [])
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return items


def _roster_get(emp_id):
    return _roster().get_item(Key={"empId": emp_id}).get("Item")


def parse_input(text):
    """'部门 姓名' / '部门 姓名 E001' / 'E001' を分解。→ (dept, name, empId)。
    単語1つ（社員番号以外）は認証入力とみなさない（=None）。"""
    toks = [t for t in re.split(r"[\s　]+", (text or "").strip()) if t]
    if not toks:
        return (None, None, None)
    if len(toks) == 1:
        if re.match(r"^[Ee]\d+$", toks[0]):
            return (None, None, toks[0].upper())
        return (None, None, None)
    dept = toks[0]
    if re.match(r"^[Ee]\d+$", toks[-1]):
        return (dept, " ".join(toks[1:-1]) or None, toks[-1].upper())
    return (dept, " ".join(toks[1:]), None)


def lookup(dept, name, emp_id):
    """roster から該当者を返す（社員番号があれば確定、なければ dept+name 完全一致）。"""
    if emp_id:
        it = _roster_get(emp_id)
        if not it:
            return []
        if name and _norm(it.get("name")) != _norm(name):
            return []
        if dept and _norm(it.get("department")) != _norm(dept):
            return []
        return [it]
    if dept and name:
        return [r for r in _scan_roster()
                if _norm(r.get("name")) == _norm(name)
                and _norm(r.get("department")) == _norm(dept)]
    return []


def is_authed(channel, user_id):
    """今日(JST)すでに認証済みなら auth レコードを返す。なければ None。"""
    it = _auth().get_item(Key={"pk": "%s#%s" % (channel, user_id)}).get("Item")
    return it if (it and it.get("authedDate") == today_jst()) else None


def record(channel, user_id, item):
    _auth().put_item(Item={
        "pk": "%s#%s" % (channel, user_id),
        "authedDate": today_jst(),
        "expireAt": _expire_epoch(),          # DynamoDB TTL（2日後自動削除）
        "empId": item.get("empId"),
        "name": item.get("name"),
        "department": item.get("department"),
        "role": item.get("role", ""),
        "userId": user_id,
        "channel": channel,
    })


def find_by_line(user_id):
    """この LINE アカウントに既に紐付いた花名册エントリ（lineUserId 一致）。なければ None。"""
    for r in _scan_roster():
        if r.get("lineUserId") == user_id:
            return r
    return None


def bind_line(emp_id, user_id):
    """花名册に lineUserId を排他的に記録（1人=1社員番号）。
    同じ userId が他のエントリに付いていれば先に外す。"""
    try:
        for r in _scan_roster():
            if r.get("lineUserId") == user_id and r.get("empId") != emp_id:
                _roster().update_item(Key={"empId": r["empId"]}, UpdateExpression="REMOVE lineUserId")
        _roster().update_item(
            Key={"empId": emp_id},
            UpdateExpression="SET lineUserId=:u",
            ExpressionAttributeValues={":u": user_id},
        )
    except Exception:  # noqa: BLE001
        pass


def gate(channel, user_id, text, role_pred):
    """日次認証ゲートの統一ロジック（一次绑定 + ワンタップ + 占用ロック）。

    戻り値 (action, item|None):
      'pass'       … 本日すでに認証済み（そのまま処理へ）
      'ok'         … 今ここで認証成功（item を返す）
      'tap'        … この LINE は既に紐付け済 → 「認証」タップを促す（再入力不要）
      'need_bind'  … 未紐付け → 初回は「部门 姓名」で本人確認が必要
      'not_found'  … 花名册に該当なし
      'ambiguous'  … 同部門同姓名が複数（社員番号を要求）
      'wrong_role' … 在籍するが当 channel の権限なし
      'taken'      … その社員番号は別の LINE アカウントで登録済み（→人事へ）
    """
    if is_authed(channel, user_id):
        return ("pass", None)

    t = (text or "").strip()
    bound = find_by_line(user_id)

    # ① ワンタップ認証（既に紐付け済みのアカウント）
    if t in TAP_WORDS:
        if not bound:
            return ("need_bind", None)
        if not role_pred(bound):
            return ("wrong_role", bound)
        record(channel, user_id, bound)
        return ("ok", bound)

    # ② 「部门 姓名」での本人確認（初回紐付け or 明示再認証）
    dept, name, emp_id = parse_input(t)
    if emp_id or (dept and name):
        matches = lookup(dept, name, emp_id)
        if len(matches) == 0:
            return ("not_found", None)
        if len(matches) > 1:
            return ("ambiguous", None)
        item = matches[0]
        existing = item.get("lineUserId")
        if existing and existing != user_id:        # 占用ロック：別アカウントが登録済み
            return ("taken", item)
        if not role_pred(item):
            return ("wrong_role", item)
        record(channel, user_id, item)
        bind_line(item["empId"], user_id)
        return ("ok", item)

    # ③ それ以外：紐付け済なら「タップを促す」、未紐付けなら「部门 姓名」を促す
    return ("tap", bound) if bound else ("need_bind", None)
