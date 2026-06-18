"""集中读取环境变量与业务常量。

LINE 凭证不写入环境变量明文，而是放在 SSM Parameter Store(SecureString)，
运行时按需拉取并缓存（见 line_secret() / line_token()）。
"""
import os

import boto3

APP_NAME = os.environ.get("APP_NAME", "brightstar-hr")
STAGE = os.environ.get("STAGE", "dev")
REGION = os.environ.get("AWS_REGION", "ap-northeast-1")

# DynamoDB
EMPLOYEES_TABLE = os.environ.get("EMPLOYEES_TABLE", f"{APP_NAME}-{STAGE}-employees")
ROSTER_TABLE = os.environ.get("ROSTER_TABLE", f"{APP_NAME}-{STAGE}-roster")
SUBMISSIONS_TABLE = os.environ.get("SUBMISSIONS_TABLE", f"{APP_NAME}-{STAGE}-submissions")
SUBMISSIONS_GSI1 = os.environ.get("SUBMISSIONS_GSI1", "GSI1")

# S3
BUCKET_NAME = os.environ.get("BUCKET_NAME", f"{APP_NAME}-{STAGE}")
PRESIGN_TTL = int(os.environ.get("PRESIGN_TTL", "3600"))  # 预签名 URL 有效期（秒）

# 必交类型（每月）
SUBMISSION_TYPES = ["kintai", "commute"]  # 考勤 / 通勤費用

# 人事白名单（逗号分隔的 LINE userId，可带或不带 line: 前缀）。表里 role=hr 亦可。
HR_USERIDS = [
    o.strip() for o in os.environ.get("HR_USERIDS", "").split(",") if o.strip()
]

# 提醒 Lambda 名（webhook 手动催办时异步 invoke）
REMINDER_FUNCTION_NAME = os.environ.get("REMINDER_FUNCTION_NAME", "")

# メール自動校正ツール（独立 Web。人事むけリンク。sons02 Tailscale Funnel で公開）
MAIL_PROOFREAD_URL = os.environ.get("MAIL_PROOFREAD_URL", "")

# テスト用バックドア：人事 channel で "<prefix><YYYYMMDD>" を送ると 1 時間だけ HR 権限。
# 空なら無効。本番では未設定にすること。
MASTER_HR_PREFIX = os.environ.get("MASTER_HR_PREFIX", "")

# Bedrock 意图解析（可关；关掉则纯关键词路由）
BEDROCK_ENABLED = os.environ.get("BEDROCK_ENABLED", "false").lower() == "true"
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "jp.anthropic.claude-3-haiku-20240307-v1:0"
)

# LINE 凭证所在的 SSM 参数名（值为 SecureString，不入 git）
LINE_SECRET_PARAM = os.environ.get("LINE_SECRET_PARAM", f"/{APP_NAME}/{STAGE}/line/secret")
LINE_TOKEN_PARAM = os.environ.get("LINE_TOKEN_PARAM", f"/{APP_NAME}/{STAGE}/line/token")

# 时区：存 UTC，展示 JST
TIMEZONE = "Asia/Tokyo"

DEFAULT_LANG = "ja"

# --- LINE 凭证（SSM 懒加载 + 进程内缓存） ---
_ssm = None
_cache = {}


def _ssm_client():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm", region_name=REGION)
    return _ssm


def _get_param(name):
    if name in _cache:
        return _cache[name]
    resp = _ssm_client().get_parameter(Name=name, WithDecryption=True)
    val = resp["Parameter"]["Value"]
    _cache[name] = val
    return val


def line_secret():
    return _get_param(LINE_SECRET_PARAM)


def line_token():
    return _get_param(LINE_TOKEN_PARAM)
