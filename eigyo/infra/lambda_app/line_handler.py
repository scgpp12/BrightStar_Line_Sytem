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
_AUTH_TAP = ("本日の本人確認をお願いします。メニューの「本日認証」を押すか「認証」と送信してください。\n"
             "请进行今日本人确认：点「本日認証」或发送「認証」。")
_AUTH_TAKEN = "この社員番号は別の LINE アカウントで登録済みです。人事にご連絡ください。\n该员工编号已绑定别的账号，请联系人事。"


def _sales_pred(item) -> bool:
    return item.get("role") == "sales" or "営業" in (item.get("department") or "")


def _auth_message(action: str, item) -> str:
    if action == "ok":
        return ("✅ 認証OK：%s（%s）\n現場の駅名を送ってください（例：東京 / 新宿）。\n"
                "认证通过，请发送现场站名。" % (item.get("name", ""), item.get("department", "")))
    return {"wrong_role": _AUTH_WRONG, "not_found": _AUTH_NOT_FOUND,
            "ambiguous": _AUTH_AMBIGUOUS, "tap": _AUTH_TAP,
            "taken": _AUTH_TAKEN}.get(action, _AUTH_PROMPT)

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


# ============== 要員CSV ダウンロード / アップロード ==============
import csv as _csv
import io as _io
import time as _time
import urllib.parse as _urlparse

from staff.models import Staff, StaffStatus  # noqa: E402

_s3 = boto3.client("s3")
_DL_CMDS = {"要員dl", "要員一覧dl", "要員csv", "名簿dl", "名簿", "ダウンロード", "dl",
            "要員ダウンロード", "csv", "要員リスト"}
_CSV_HEADER = ["staff_id", "name", "nearest_station", "department", "status"]


def _repo() -> DynamoDBStaffRepository:
    return DynamoDBStaffRepository()


def _bucket() -> str:
    return os.environ["STAFF_BUCKET"]


def _sign_key(key: str) -> str:
    return hmac.new(_cred("LINE_SECRET_PARAM").encode("utf-8"),
                    key.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _dl_link(base: str, key: str) -> str:
    return "%s/dl?key=%s&sig=%s" % (base, _urlparse.quote(key, safe=""), _sign_key(key))


def _build_staff_csv() -> str:
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(_CSV_HEADER)
    for s in _repo().list():
        w.writerow([s.staff_id, s.name, s.nearest_station, s.department or "", s.status.value])
    return buf.getvalue()


def _export_to_s3() -> str:
    key = "exports/staff_%s.csv" % _time.strftime("%Y%m%d_%H%M%S")
    _s3.put_object(Bucket=_bucket(), Key=key,
                   Body=_build_staff_csv().encode("utf-8-sig"),  # Excel 用 BOM
                   ContentType="text/csv; charset=utf-8")
    return key


def _download_content(message_id: str) -> bytes | None:
    url = "https://api-data.line.me/v2/bot/message/%s/content" % message_id
    req = urllib.request.Request(url, method="GET",
                                 headers={"Authorization": "Bearer " + _cred("LINE_TOKEN_PARAM")})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except Exception:  # noqa: BLE001
        return None


def _import_staff_csv(data: bytes) -> tuple[int, list[str]]:
    """CSV → StaffTable に upsert。戻り値 (反映件数, エラー行メッセージ)。"""
    text = data.decode("utf-8-sig", "ignore")
    rows = list(_csv.DictReader(_io.StringIO(text)))
    staffs, errs = [], []
    for i, row in enumerate(rows, start=2):
        sid = (row.get("staff_id") or "").strip()
        name = (row.get("name") or "").strip()
        station = (row.get("nearest_station") or "").strip()
        if not sid or not name:
            errs.append("%d行目: staff_id と name は必須" % i)
            continue
        if not station and (row.get("address") or "").strip():
            try:
                from transit.geo import nearest_station as _ns
                station = _ns(row["address"].strip())["station"]
            except Exception as e:  # noqa: BLE001
                errs.append("%d行目: 住所→駅 変換失敗(%s)" % (i, sid))
                continue
        if not station:
            errs.append("%d行目: nearest_station か address が必要(%s)" % (i, sid))
            continue
        try:
            st = StaffStatus.from_str((row.get("status") or "available").strip() or "available")
        except ValueError:
            st = StaffStatus.AVAILABLE
        staffs.append(Staff(staff_id=sid, name=name, nearest_station=station,
                            status=st, department=(row.get("department") or "").strip() or None))
    n = _repo().bulk_upsert(staffs) if staffs else 0
    return n, errs


def _handle_dl(event: dict) -> dict:
    qs = event.get("queryStringParameters") or {}
    key, sig = qs.get("key"), qs.get("sig")
    if key and sig and hmac.compare_digest(_sign_key(key), sig):
        url = _s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": _bucket(), "Key": key,
                    "ResponseContentDisposition": 'attachment; filename="staff.csv"',
                    "ResponseContentType": "text/csv; charset=utf-8"},
            ExpiresIn=600)
        return {"statusCode": 302, "headers": {"Location": url}, "body": ""}
    return {"statusCode": 404, "body": "not found"}


def _base_url(event: dict) -> str:
    h = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    host = h.get("host") or (event.get("requestContext", {}) or {}).get("domainName", "")
    return "https://" + host


def handler(event: dict, context) -> dict:
    # --- 非同期ワーカー呼び出し（自己 invoke で来る） ---
    if event.get("_worker"):
        _worker(event.get("text", ""), event.get("userId", ""))
        return {"statusCode": 200, "body": "worker done"}

    # --- GET /dl?key=&sig= → 要員CSV ダウンロード（HMAC 署名付き短縮リンク）---
    if ((event.get("requestContext", {}).get("http", {}) or {}).get("method", "POST")) == "GET":
        return _handle_dl(event)

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

    base = _base_url(event)
    for ev in payload.get("events", []):
        if ev.get("type") != "message":
            continue
        m = ev.get("message", {}) or {}
        mtype = m.get("type")
        if mtype not in ("text", "file"):
            continue
        text = (m.get("text", "") or "").strip() if mtype == "text" else ""
        reply_token = ev.get("replyToken")
        user_id = (ev.get("source") or {}).get("userId")
        if not reply_token:
            continue

        def _r(msg):  # 返信（失敗握り潰し）
            try:
                _reply(reply_token, msg)
            except Exception:  # noqa: BLE001
                pass

        # --- 登録解除 ---
        if user_id and text in authlib.RESET_WORDS:
            authlib.unbind("line:" + user_id)
            _r("認証の紐付けを解除しました。次回「営業部 お名前」で認証してください。\n已解除认证绑定。")
            continue

        # --- 日次認証ゲート（text/file 共通。file は text="" で判定）---
        auth_uid = ("line:" + user_id) if user_id else None
        action, item = authlib.gate(CHANNEL, auth_uid, text, _sales_pred) if auth_uid else ("need_bind", None)
        if action != "pass":
            _r(_auth_message(action, item))
            continue

        # === 認証済み：分岐 ===
        # ① CSV ファイル受信 → 要員表を更新
        if mtype == "file":
            if not (m.get("fileName") or "").lower().endswith(".csv"):
                _r("要員リストの CSV ファイル(.csv)を送ってください。")
                continue
            data = _download_content(m.get("id"))
            if not data:
                _r("ファイルの取得に失敗しました。もう一度お試しください。")
                continue
            try:
                n, errs = _import_staff_csv(data)
            except Exception as e:  # noqa: BLE001
                _r("CSV の解析に失敗しました: %s" % type(e).__name__)
                continue
            msg = "✅ 要員 %d 件を反映しました（追加・更新）。" % n
            if errs:
                msg += "\n⚠️ スキップ %d 件:\n" % len(errs) + "\n".join(errs[:8])
            _r(msg)
            continue

        # ② ダウンロードコマンド → CSV エクスポート + リンク
        if text.lower() in _DL_CMDS:
            try:
                key = _export_to_s3()
                _line_post(_LINE_REPLY_URL, {"replyToken": reply_token, "messages": [{
                    "type": "template", "altText": "要員リストCSV",
                    "template": {"type": "buttons",
                                 "text": "要員リスト(CSV)をDL。編集して送り返すと反映されます",
                                 "actions": [{"type": "uri", "label": "CSVダウンロード",
                                              "uri": _dl_link(base, key)}]}}]})
            except Exception as e:  # noqa: BLE001
                print("[DL] export error:", repr(e))
                _r("エクスポートに失敗しました: %s" % type(e).__name__)
            continue

        # ③ それ以外のテキスト → 現場の通勤比較（従来）
        if user_id:
            _r(_ACK_TEXT)
            try:
                _lambda.invoke(
                    FunctionName=context.function_name, InvocationType="Event",
                    Payload=json.dumps({"_worker": 1, "text": text, "userId": user_id}).encode("utf-8"))
            except Exception:  # noqa: BLE001
                _worker_sync_fallback(reply_token, text)
        else:
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
