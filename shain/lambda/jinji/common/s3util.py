"""S3 提交物存取 + 预签名 URL。

布局（用户规格）：
  hr/{yyyy}/{mm}/worktimes/勤務表（{姓名}）_{YYYYMM}.xlsx        (kintai)
  hr/{yyyy}/{mm}/expenses/交通費経費【{YYYY}年{MM}月{姓名}】.xlsx   (commute)
  hr/template/勤務表.xlsx / hr/template/交通費経費.xlsx     (空白样式)
提交对象统一打标签 lifecycle=managed（生命周期规则按此标签归档/删除，模板不受影响）。
桶开版本管理 → 允许重复提交，旧版本保留。
"""
import io
import urllib.parse
import zipfile

import boto3

from . import config

_s3 = boto3.client("s3", region_name=config.REGION)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUBMISSION_TAG = "lifecycle=managed"

MIME_BY_EXT = {
    "xlsx": XLSX_MIME, "pdf": "application/pdf",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "zip": "application/zip", "bin": "application/octet-stream",
}


def detect_format(data):
    """バイト先頭からファイル種別を推定 → (ext, mime)。xlsx(zip)/pdf/jpg/png。"""
    b = data or b""
    if b[:4] == b"PK\x03\x04":
        return "xlsx", XLSX_MIME
    if b[:4] == b"%PDF":
        return "pdf", "application/pdf"
    if b[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    return "bin", "application/octet-stream"


def content_type_for(key):
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    return MIME_BY_EXT.get(ext, XLSX_MIME)

# 类型 → 目录 / 显示名 / 模板 key
TYPE_META = {
    "kintai":  {"folder": "worktimes", "label": "勤務表",
                "template": "hr/template/勤務表.xlsx", "ascii": "kintai.xlsx"},
    "commute": {"folder": "expenses",  "label": "交通費経費",
                "template": "hr/template/交通費経費.xlsx", "ascii": "kotsuhi.xlsx",
                "template_name": "交通費経費【XX年XX月申請人(漢字)】.xlsx"},
    "other":   {"folder": "others",    "label": "その他経費",
                "template": "", "ascii": "other"},
}


def _disposition(display_name, ascii_fallback="download.xlsx"):
    """Content-Disposition：ascii 兜底 + RFC5987 UTF-8 文件名（日文也能正确命名）。"""
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (
        ascii_fallback, urllib.parse.quote(display_name),
    )


def _safe(part):
    return (part or "").replace(":", "_").replace("/", "_").strip()


def submission_filename(period, type_, name, ext="xlsx", eid=""):
    """ファイル名に社員ID を含める（同姓同名対策）：勤務表（E003_山田太郎）_YYYYMM.xlsx"""
    meta = TYPE_META[type_]
    nm = _safe(name) or "noname"
    if eid:
        nm = "%s_%s" % (_safe(eid), nm)
    if type_ == "commute":
        # 交通費経費【YYYY年MM月+社員ID_氏名】.{ext}
        return "交通費経費【%s年%s月%s】.%s" % (period[:4], period[4:], nm, ext)
    return "%s（%s）_%s.%s" % (meta["label"], nm, period, ext)


def submission_key(period, type_, name, ext="xlsx", eid=""):
    yyyy, mm = period[:4], period[4:]
    meta = TYPE_META[type_]
    return "hr/%s/%s/%s/%s" % (yyyy, mm, meta["folder"],
                               submission_filename(period, type_, name, ext, eid))


def put_submission(period, type_, name, data, ext="xlsx", mime=None, eid=""):
    """存入提交物（带类型 + 生命周期标签），返回 (key, filename)。
    ext/mime 支持 xlsx 以外（PDF/画像）。eid=社員ID（文件名防重名）。"""
    key = submission_key(period, type_, name, ext, eid)
    _s3.put_object(Bucket=config.BUCKET_NAME, Key=key, Body=data,
                   ContentType=mime or XLSX_MIME, Tagging=SUBMISSION_TAG)
    return key, key.rsplit("/", 1)[-1]


def other_key(period, name, purpose, ext, ts):
    """その他経費：hr/{yyyy}/{mm}/others/{用途}_{氏名}_{period}_{ts}.{ext}（月内複数可）。"""
    yyyy, mm = period[:4], period[4:]
    stem = "%s_%s_%s_%s" % (_safe(purpose) or "その他", _safe(name) or "noname", period, ts)
    return "hr/%s/%s/others/%s.%s" % (yyyy, mm, stem, ext)


def put_other(period, name, purpose, data, ext, mime, ts):
    key = other_key(period, name, purpose, ext, ts)
    _s3.put_object(Bucket=config.BUCKET_NAME, Key=key, Body=data,
                   ContentType=mime or "application/octet-stream", Tagging=SUBMISSION_TAG)
    return key, key.rsplit("/", 1)[-1]


def presign_get(key, ttl=None, download_name=None, ascii_name="download.xlsx",
                content_type=None):
    """预签名下载 URL。强制类型 + 指定文件名，避免 iOS 存成 .txt。"""
    params = {
        "Bucket": config.BUCKET_NAME,
        "Key": key,
        "ResponseContentType": content_type or XLSX_MIME,
    }
    if download_name:
        params["ResponseContentDisposition"] = _disposition(download_name, ascii_name)
    return _s3.generate_presigned_url(
        "get_object", Params=params, ExpiresIn=ttl or config.PRESIGN_TTL,
    )


def presign_template(type_):
    """空白样式下载链接，带正确文件名与类型。"""
    meta = TYPE_META.get(type_)
    if not meta:
        return ""
    download_name = meta.get("template_name") or ("%s.xlsx" % meta["label"])
    return presign_get(meta["template"],
                       download_name=download_name, ascii_name=meta["ascii"])


# --- 待分类暂存（用户发了文件但还没说是哪类） ---

def pending_key(user_id, file_name):
    return "pending/%s/%s" % (_safe(user_id), _safe(file_name) or "file.xlsx")


def put_pending(user_id, file_name, data):
    key = pending_key(user_id, file_name)
    _s3.put_object(Bucket=config.BUCKET_NAME, Key=key, Body=data)
    return key


def read_object(key):
    """S3 オブジェクトのバイト列を取得（取得失敗は None）。"""
    try:
        return _s3.get_object(Bucket=config.BUCKET_NAME, Key=key)["Body"].read()
    except Exception:  # noqa: BLE001
        return None


def copy_to_submission(pending_k, period, type_, name, eid=""):
    """把暂存文件转正到 hr/ 路径（带类型 + 标签），删除暂存，返回 (key, filename)。"""
    key = submission_key(period, type_, name, eid=eid)
    _s3.copy_object(
        Bucket=config.BUCKET_NAME,
        CopySource={"Bucket": config.BUCKET_NAME, "Key": pending_k},
        Key=key,
        MetadataDirective="REPLACE", ContentType=XLSX_MIME,
        TaggingDirective="REPLACE", Tagging=SUBMISSION_TAG,
    )
    try:
        _s3.delete_object(Bucket=config.BUCKET_NAME, Key=pending_k)
    except Exception:  # noqa: BLE001
        pass
    return key, key.rsplit("/", 1)[-1]


def build_month_zip(period):
    """把某月全部提交打包成一个 zip，存 exports/{period}.zip，返回 (zipkey, 文件数)。
    zip 内路径 = {folder}/{filename}（重名加序号）。"""
    yyyy, mm = period[:4], period[4:]
    prefix = "hr/%s/%s/" % (yyyy, mm)
    paginator = _s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=config.BUCKET_NAME, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    if not keys:
        return None, 0

    buf = io.BytesIO()
    seen = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for k in keys:
            body = _s3.get_object(Bucket=config.BUCKET_NAME, Key=k)["Body"].read()
            arc = k[len(prefix):]                        # {folder}/{filename}
            if arc in seen:
                seen[arc] += 1
                stem, _, ext = arc.rpartition(".")
                arc = "%s_%d.%s" % (stem or arc, seen[arc], ext or "xlsx")
            else:
                seen[arc] = 0
            zf.writestr(arc, body)
    buf.seek(0)
    zipkey = "exports/%s.zip" % _safe(period)
    _s3.put_object(Bucket=config.BUCKET_NAME, Key=zipkey, Body=buf.getvalue(),
                   ContentType="application/zip")
    return zipkey, len(keys)
