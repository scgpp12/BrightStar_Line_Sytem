"""全社花名册ベースの「部门 姓名」日次認証（4 channel 共有・自己完結モジュール）。

身份源 = roster テーブル（env ROSTER_TABLE）：empId/name/department/role/lineUserId。
認証状態 = auth テーブル（env AUTH_TABLE, PK 'pk' = '<channel>#<userId>'）に authedDate(JST) を保存。
日次：authedDate != 今日(JST) なら再認証が必要。

依存は boto3 + 標準ライブラリのみ。各 channel はこのファイルをそのまま vendoring して使う。
利用側は authenticate(channel, user_id, text, role_pred) を呼び、status で分岐する。
"""
import os
import re
import unicodedata
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


def has_role(item, role):
    """役割判定：role(主) と roles(複数・CSV/リスト) のどちらかに含まれれば True。
    一人が hr,teacher など複数役割を持てる（社員変更 E001 役割 hr,講師）。"""
    if not item:
        return False
    role = (role or "").strip().lower()
    if (str(item.get("role") or "")).strip().lower() == role:
        return True
    rs = item.get("roles")
    if isinstance(rs, str):
        import re as _re
        rs = _re.split(r"[,，、/\s　]+", rs)
    return role in [str(x).strip().lower() for x in (rs or []) if str(x).strip()]


def today_jst():
    return datetime.now(JST).strftime("%Y-%m-%d")


def _now_epoch():
    return int(datetime.now(JST).timestamp())


def master_code_today(prefix):
    """テスト用バックドアコード = prefix + YYYYMMDD(JST)。prefix 空なら無効(None)。"""
    return (prefix + datetime.now(JST).strftime("%Y%m%d")) if prefix else None


def grant_temp(channel, user_id, *, name="テストHR", role="hr", seconds=3600):
    """期限付き認証を付与（validUntil=now+seconds）。花名册の紐付けは変更しない。"""
    until = _now_epoch() + int(seconds)
    _auth().put_item(Item={
        "pk": "%s#%s" % (channel, user_id),
        "authedDate": today_jst(),
        "validUntil": until,
        "expireAt": _now_epoch() + 2 * 86400,
        "name": name, "role": role, "userId": user_id, "channel": channel, "temp": True,
    })
    return until


def _expire_epoch():
    """auth 行の TTL（2日後）。DynamoDB が自動削除する。"""
    return int(datetime.now(JST).timestamp()) + 2 * 86400


# 「本日認証」ワンタップ用キーワード（Rich Menu のボタンも同文言を送る）
TAP_WORDS = {"認証", "认证", "本日認証", "本人確認", "本日認証する", "はい", "ok", "OK", "確認", "出勤"}

# 「登録解除 / 別人で登録し直す」キーワード（紐付けを解除して再登録できる）
RESET_WORDS = {"登録解除", "登録変更", "別アカウント", "リセット", "解除",
               "登録取消", "登録削除", "紐付け解除", "アカウント解除", "ログアウト",
               "重新登録", "重新注册", "重新登记", "切换身份", "切換身分", "登録し直す",
               "解绑", "解除绑定", "取消绑定", "解除綁定", "取消綁定", "登录解除", "解除登录",
               "unbind", "logout", "reset"}

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


def mark_blocked(user_id, blocked):
    """ユーザーが bot をブロック/削除(unfollow)/再追加(follow)した時、
    roster の blocked フラグを更新する。lineUserId 一致のエントリに付ける。
    戻り値＝氏名 or None（未登録の相手）。"""
    if not user_id:
        return None
    name = None
    for r in _scan_roster():
        if r.get("lineUserId") != user_id:
            continue
        name = r.get("name")
        try:
            if blocked:
                _roster().update_item(
                    Key={"empId": r["empId"]},
                    UpdateExpression="SET #b=:b, #t=:t",
                    ExpressionAttributeNames={"#b": "blocked", "#t": "blockedAt"},
                    ExpressionAttributeValues={":b": True, ":t": today_jst()})
            else:
                _roster().update_item(
                    Key={"empId": r["empId"]},
                    UpdateExpression="REMOVE #b, #t",
                    ExpressionAttributeNames={"#b": "blocked", "#t": "blockedAt"})
        except Exception:  # noqa: BLE001
            pass
    return name


def list_blocked():
    """現在ブロック中（blocked=True）の roster エントリ一覧。"""
    return [r for r in _scan_roster() if r.get("blocked")]


def _norm(s):
    """姓名/部署の表記ゆれ吸収：NFKC(全/半角)＋空白除去＋小文字化＋カタカナ→ひらがな。
    これで『ソンセイコウ』『そんせいこう』、全角/半角、ローマ字の大小は同一視できる。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\s　]+", "", s).strip().lower()
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def _name_variants(item):
    """本人の氏名表記候補（本名 ＋ 別名/読み aliases）を正規化集合で返す。
    aliases は花名册の任意項目（『孫成功,ソンセイコウ』等。区切りはカンマ/読点/空白/|）。
    中文(簡/繁)・日本語かな・ハングル・ローマ字いずれも alias に入れれば一致する。"""
    vals = [item.get("name")]
    al = item.get("aliases")
    if al:
        vals += re.split(r"[,，、|;；/\s　]+", al)
    return {_norm(v) for v in vals if v}


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
        if name and _norm(name) not in _name_variants(it):
            return []
        if dept and _norm(it.get("department")) != _norm(dept):
            return []
        return [it]
    if dept and name:
        return [r for r in _scan_roster()
                if _norm(name) in _name_variants(r)
                and _norm(r.get("department")) == _norm(dept)]
    return []


def is_authed(channel, user_id):
    """認証済みなら auth レコードを返す。なければ None。
    validUntil(期限付き=テストHRバックドア)があればその時刻まで有効、無ければ当日(JST)有効。"""
    it = _auth().get_item(Key={"pk": "%s#%s" % (channel, user_id)}).get("Item")
    if not it:
        return None
    vu = it.get("validUntil")
    if vu is not None:
        return it if int(vu) > _now_epoch() else None
    return it if it.get("authedDate") == today_jst() else None


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
