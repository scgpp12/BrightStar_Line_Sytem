"""课程网站 API（供 Vercel/Cloudflare 静态站调用，CORS 开放）。

POST /web/login       {code}                         → 校验登录码 {ok,name,role}
POST /web/submit      {code, kind, refId, data, score?} → 记录成绩/问卷(按学员)
POST /web/my-results  {code}                          → 本人已提交记录
POST /web/results     {code, kind, refId}             → 老师导出某项全部学员结果
身份一律用登录码解析，避免前端伪造。成绩/问卷按学员 openid 入库。
"""
import decimal
import json

from boto3.dynamodb.conditions import Key

from kenshu.common import business, config, db
from kenshu.common.timeutils import iso_utc

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


class _Dec(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **_CORS},
        "body": json.dumps(body, ensure_ascii=False, cls=_Dec),
    }


def _to_ddb(obj):
    return json.loads(json.dumps(obj), parse_float=decimal.Decimal)


def _body(event):
    try:
        return json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        return {}


# --------------------------------- 入口 ---------------------------------
def handler(event, context):
    http = event["requestContext"]["http"]
    if http["method"] == "OPTIONS":
        return _resp(200, {"ok": True})
    path = http.get("path", "")
    data = _body(event)
    student = business.find_by_login_code(data.get("code", ""))
    if not student:
        return _resp(401, {"ok": False, "message": "登录码无效，请在微信发「登录码」获取"})

    if path.endswith("/login"):
        return _resp(200, {"ok": True, "name": student.get("name", ""), "role": student.get("role", "student")})

    if path.endswith("/submit"):
        return _submit(student, data)

    if path.endswith("/my-results"):
        return _my_results(student)

    if path.endswith("/results"):
        return _teacher_results(student, data)

    return _resp(404, {"ok": False, "message": "not found"})


def _submit(student, data):
    kind = (data.get("kind") or "").strip()      # quiz | survey
    ref_id = (data.get("refId") or "").strip()   # lesson1 ...
    if kind not in ("quiz", "survey") or not ref_id:
        return _resp(400, {"ok": False, "message": "缺少 kind(quiz/survey) 或 refId"})
    item = {
        "openid": student["openid"],
        "itemKey": f"{kind}#{ref_id}",
        "kind": kind,
        "refId": ref_id,
        "name": student.get("name", ""),
        "data": _to_ddb(data.get("data", {})),
        "submittedAt": iso_utc(),
    }
    if "score" in data and data["score"] is not None:
        item["score"] = _to_ddb(data["score"])
    db.results().put_item(Item=item)  # 同一项重复提交=覆盖(最新)
    return _resp(200, {"ok": True, "message": "已记录", "name": student.get("name", "")})


def _my_results(student):
    rows = db.results().query(
        KeyConditionExpression=Key("openid").eq(student["openid"])
    ).get("Items", [])
    return _resp(200, {"ok": True, "results": rows})


def _teacher_results(student, data):
    if student.get("role") != "teacher":
        return _resp(403, {"ok": False, "message": "仅老师可导出成绩"})
    kind = (data.get("kind") or "").strip()
    ref_id = (data.get("refId") or "").strip()
    if not kind or not ref_id:
        return _resp(400, {"ok": False, "message": "缺少 kind 或 refId"})
    rows = db.results().query(
        IndexName="GSI1",
        KeyConditionExpression=Key("itemKey").eq(f"{kind}#{ref_id}"),
    ).get("Items", [])
    out = [{"name": r.get("name", ""), "score": r.get("score"), "data": r.get("data"),
            "submittedAt": r.get("submittedAt")} for r in rows]
    return _resp(200, {"ok": True, "count": len(out), "results": out})
