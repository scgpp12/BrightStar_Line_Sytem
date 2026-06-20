"""日次リコンサイル：bot をブロック/削除したユーザーを検出し紐付けを解除する。

毎日 0:00(JST) に EventBridge から起動。各 roster.lineUserId について 4 channel の
LINE profile API（GET /v2/bot/profile/{userId}）を叩き、**全 channel で到達不可**
（401/403/404 ＝ ブロック/削除/未フォロー）なら、そのユーザーの紐付け(lineUserId)
＋全 channel の認証行をクリアする（authlib.unbind）。

誤クリア防止：一時的なネットワーク障害や 429/5xx 等の不確実な応答は「到達可能」
として扱い、明確に 401/403/404 が全 channel で返ったときだけクリアする。
"""
import os
import urllib.error
import urllib.request

import boto3

from common import authlib

_ssm = boto3.client("ssm")

_PARAMS = {
    "kenshu": os.environ.get("TOKEN_PARAM_KENSHU", ""),
    "jinji": os.environ.get("TOKEN_PARAM_JINJI", ""),
    "eigyo": os.environ.get("TOKEN_PARAM_EIGYO", ""),
    "shain": os.environ.get("TOKEN_PARAM_SHAIN", ""),
}
_tok_cache: dict = {}


def _token(chan):
    p = _PARAMS.get(chan)
    if not p:
        return None
    if chan not in _tok_cache:
        try:
            _tok_cache[chan] = _ssm.get_parameter(
                Name=p, WithDecryption=True)["Parameter"]["Value"]
        except Exception as e:  # noqa: BLE001
            print("[reconcile] token取得失敗", chan, repr(e))
            _tok_cache[chan] = None
    return _tok_cache[chan]


def _reachable(uid_bare):
    """いずれかの channel で profile が取れれば到達可能(=ブロックしていない)。

    全 channel が 401/403/404 を返したときだけ False（ブロック/削除）。
    一時障害・429/5xx 等は安全側に True（クリアしない）。"""
    saw_token = False
    for chan in _PARAMS:
        tok = _token(chan)
        if not tok:
            continue
        saw_token = True
        req = urllib.request.Request(
            "https://api.line.me/v2/bot/profile/" + uid_bare,
            headers={"Authorization": "Bearer " + tok})
        try:
            urllib.request.urlopen(req, timeout=10)
            return True                        # 200 → 到達可能
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                continue                       # この channel は未フォロー/ブロック
            return True                        # 429/5xx 等は不確実 → 誤クリア回避
        except Exception:                      # noqa: BLE001
            return True                        # 一時障害 → 誤クリア回避
    if not saw_token:
        return True                            # 判定不能 → クリアしない
    return False                               # 全 channel で 401/403/404


def handler(event, context):
    cleared = []
    checked = 0
    for r in authlib._scan_roster():
        raw = r.get("lineUserId")
        if not raw:
            continue
        checked += 1
        uid = raw[5:] if raw.startswith("line:") else raw
        if _reachable(uid):
            continue
        # 全 channel 到達不可 → ブロック/削除とみなし紐付け＋認証行をクリア
        name = authlib.unbind(raw)
        cleared.append({"empId": r.get("empId"),
                        "name": name or r.get("name"), "lineUserId": raw})
        print("[RECONCILE] cleared empId=%s name=%s （ブロック/削除）"
              % (r.get("empId"), name or r.get("name")))
    print("[RECONCILE] done. checked=%d cleared=%d" % (checked, len(cleared)))
    return {"checked": checked, "cleared": cleared}
