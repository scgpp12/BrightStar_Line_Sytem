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
        "empId": item.get("empId"),
        "name": item.get("name"),
        "department": item.get("department"),
        "role": item.get("role", ""),
        "userId": user_id,
        "channel": channel,
    })


def bind_line(emp_id, user_id):
    """花名册に lineUserId を記録（存在すれば。提出突合などに使う）。"""
    try:
        _roster().update_item(
            Key={"empId": emp_id},
            UpdateExpression="SET lineUserId=:u",
            ExpressionAttributeValues={":u": user_id},
        )
    except Exception:  # noqa: BLE001
        pass


def authenticate(channel, user_id, text, role_pred):
    """日次認証を試みる。

    戻り値 (status, item|None):
      'need_input'  … 認証入力の形でない（「部门 姓名」を促す）
      'not_found'   … 花名册に該当なし（→人事へ）
      'ambiguous'   … 同部門同姓名が複数（→社員番号を要求）
      'wrong_role'  … 在籍するが当 channel の権限なし
      'ok'          … 認証成功（item を返す。auth 記録 + lineUserId 紐付け済）
    """
    dept, name, emp_id = parse_input(text)
    if not (emp_id or (dept and name)):
        return ("need_input", None)
    matches = lookup(dept, name, emp_id)
    if len(matches) == 0:
        return ("not_found", None)
    if len(matches) > 1:
        return ("ambiguous", None)
    item = matches[0]
    if not role_pred(item):
        return ("wrong_role", item)
    record(channel, user_id, item)
    bind_line(item["empId"], user_id)
    return ("ok", item)
