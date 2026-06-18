"""LINE Webhook ハンドラ（営業アシスタント用）.

LINE で現場の駅名を送ると、待機中(available)の全要員の通勤コスト比較を返信する。

セキュリティ:
  - channel secret / access token は **SSM SecureString** から読む（コード・環境変数に値を置かない）。
  - 受信は LINE の `X-Line-Signature`（channel secret での HMAC-SHA256）を検証してから処理。
  - この Function URL は authType=NONE（公開）だが、署名検証で正当な LINE 以外を弾く。

注意（プロトタイプ）:
  ekitan スクレイピングは sleep を挟むため、未キャッシュの現場で要員が多いと応答が遅くなる。
  LINE の replyToken には有効期限があるので、初回は失敗し得る（キャッシュ後は速い）。
  本格運用するなら「即 200 → push で非同期返信」に変える。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.request
from pathlib import Path

import boto3

import authlib  # 全社花名册ベースの日次認証（営業部のみ）

from transit.registry import StationRegistry  # noqa: E402（以降の import 群と一体）
from transit.ekitan_source import EkitanScraper
from transit.dynamo_cache import DynamoDBCache
from transit.batch import compare_site
from transit.models import Strategy
from transit.exceptions import TransitDataError
from staff.dynamo_repository import DynamoDBStaffRepository
from staff.models import StaffStatus

CHANNEL = "eigyo"

_AUTH_PROMPT = (
    "ご本人確認のため「所属部署 お名前」を入力してください。\n"
    "例：営業部 田中\n────────\n"
    "请输入「部门 姓名」确认本人。\n例：营业部 田中"
)
_AUTH_WRONG = (
    "ご本人確認できましたが、営業部の方ではありません。本ツールは営業部専用です。\n"
    "已确认本人，但你不是营业部，本工具仅限营业部使用。"
)
_AUTH_NOT_FOUND = "社員名簿に該当者が見つかりません。人事にご確認ください。\n花名册查无此人，请联系人事。"
_AUTH_AMBIGUOUS = "同部署・同氏名が複数います。社員番号も付けてください（例：営業部 田中 E002）。"


def _sales_pred(item) -> bool:
    return item.get("role") == "sales" or "営業" in (item.get("department") or "")


def _auth_message(status: str, item) -> str:
    if status == "ok":
        return ("✅ 認証OK：%s（%s）\n現場の駅名を送ってください（例：東京 / 新宿）。\n"
                "认证通过，请发送现场站名。" % (item.get("name", ""), item.get("department", "")))
    return {"wrong_role": _AUTH_WRONG, "not_found": _AUTH_NOT_FOUND,
            "ambiguous": _AUTH_AMBIGUOUS}.get(status, _AUTH_PROMPT)

_REGISTRY = StationRegistry.from_file(Path(__file__).resolve().parent / "stations.json")
_ssm = boto3.client("ssm")
_lambda = boto3.client("lambda")
_cred_cache: dict[str, str] = {}

_LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
_LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
_ACK_TEXT = "収集中…ちょっと待ってね 🚃"


def _cred(env_key: str) -> str:
    """SSM SecureString からクレデンシャルを取得（コールド時1回・キャッシュ）."""
    param_name = os.environ[env_key]
    if param_name not in _cred_cache:
        resp = _ssm.get_parameter(Name=param_name, WithDecryption=True)
        _cred_cache[param_name] = resp["Parameter"]["Value"]
    return _cred_cache[param_name]


def _verify(body: bytes, signature: str) -> bool:
    mac = hmac.new(_cred("LINE_SECRET_PARAM").encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode("utf-8"), signature or "")


def _line_post(url: str, body: dict) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_cred('LINE_TOKEN_PARAM')}",
        },
    )
    urllib.request.urlopen(req, timeout=10)


def _reply(reply_token: str, text: str) -> None:
    """replyToken で即時返信（ack 用。有効期限が短い）."""
    _line_post(_LINE_REPLY_URL, {"replyToken": reply_token,
                                 "messages": [{"type": "text", "text": text[:4900]}]})


def _push(user_id: str, text: str) -> None:
    """userId へ push（時間制限なし。重い結果はこちらで後送り）."""
    _line_post(_LINE_PUSH_URL, {"to": user_id,
                                "messages": [{"type": "text", "text": text[:4900]}]})


def _source() -> EkitanScraper:
    return EkitanScraper(
        cache=DynamoDBCache(),
        request_delay_sec=float(os.environ.get("REQUEST_DELAY_SEC", "3")),
        robots_cache_dir="/tmp/robots",
    )


def _build_reply(site: str) -> str:
    repo = DynamoDBStaffRepository()
    staffs = repo.list(StaffStatus.AVAILABLE)
    if not staffs:
        return "待機中(available)の要員がいません。"

    rows = compare_site(site, staffs, _source(), _REGISTRY, strategy=Strategy.CHEAPEST)
    lines = [f"■ 現場「{site}」への通勤コスト（待機中{len(rows)}名 / 定期代が安い順）", ""]
    for r in rows:
        if r.result is None:
            lines.append(f"・{r.staff.name}（{r.staff.nearest_station}）: 取得失敗")
            continue
        res = r.result
        lines.append(
            f"・{r.staff.name}（{r.staff.nearest_station}→{site}）: "
            f"{res.duration_min}分/乗換{res.transfers}回 "
            f"IC{res.fare_ic_yen:,}円 定期{res.pass_1month_yen:,}円\n"
            f"　経由: {res.route_summary}"  # 乗換駅を含む経路
        )
    return "\n".join(lines)


def _worker(text: str, user_id: str) -> None:
    """非同期側: 重い比較を実行し push で後送りする."""
    try:
        msg = _build_reply(text)
    except TransitDataError as e:
        msg = (
            f"「{text}」を現場として認識できませんでした。\n"
            f"駅名をそのまま送ってください（例: 東京 / 新宿。中国語・かな・ローマ字も可）。\n（{e}）"
        )
    except Exception as e:  # noqa: BLE001
        msg = f"エラーが発生しました: {type(e).__name__}: {e}"
    try:
        _push(user_id, msg)
    except Exception:  # noqa: BLE001
        pass


def handler(event: dict, context) -> dict:
    # --- 非同期ワーカー呼び出し（自己 invoke で来る） ---
    if event.get("_worker"):
        _worker(event.get("text", ""), event.get("userId", ""))
        return {"statusCode": 200, "body": "worker done"}

    # --- LINE Webhook 本体 ---
    raw = event.get("body") or ""
    body = base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode("utf-8")
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    if not _verify(body, headers.get("x-line-signature", "")):
        return {"statusCode": 403, "body": "invalid signature"}

    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 200, "body": "ok"}  # 検証用の空ボディ等

    for ev in payload.get("events", []):
        if ev.get("type") != "message" or ev.get("message", {}).get("type") != "text":
            continue
        text = ev["message"]["text"].strip()
        reply_token = ev.get("replyToken")
        user_id = (ev.get("source") or {}).get("userId")
        if not reply_token:
            continue

        # --- 日次認証ゲート（営業部のみ・毎日「部门 姓名」で本人確認）---
        auth_uid = ("line:" + user_id) if user_id else None
        if not auth_uid or not authlib.is_authed(CHANNEL, auth_uid):
            if auth_uid:
                status, item = authlib.authenticate(CHANNEL, auth_uid, text, _sales_pred)
            else:
                status, item = "need_input", None
            try:
                _reply(reply_token, _auth_message(status, item))
            except Exception:  # noqa: BLE001
                pass
            continue
        # --- 認証済み：現場クエリ処理 ---

        if user_id:
            # 1) 即 ack（収集中…）2) 重い処理は非同期ワーカーへ 3) すぐ 200 を返す
            try:
                _reply(reply_token, _ACK_TEXT)
            except Exception:  # noqa: BLE001
                pass
            try:
                _lambda.invoke(
                    FunctionName=context.function_name,
                    InvocationType="Event",  # 非同期
                    Payload=json.dumps({"_worker": 1, "text": text, "userId": user_id}).encode("utf-8"),
                )
            except Exception as e:  # noqa: BLE001 - 起動失敗時は同期フォールバック
                _worker_sync_fallback(reply_token, text)
        else:
            # userId が無い（push 不可）→ 同期で返信（フォールバック）
            _worker_sync_fallback(reply_token, text)

    return {"statusCode": 200, "body": "ok"}


def _worker_sync_fallback(reply_token: str, text: str) -> None:
    """非同期不可時の同期返信（遅くなり得るが最後の手段）."""
    try:
        msg = _build_reply(text)
    except Exception as e:  # noqa: BLE001
        msg = f"現場「{text}」を認識できませんでした（{type(e).__name__}）。"
    try:
        _reply(reply_token, msg)
    except Exception:  # noqa: BLE001
        pass
