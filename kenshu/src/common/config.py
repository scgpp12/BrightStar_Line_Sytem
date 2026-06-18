"""集中读取环境变量与业务常量。"""
import os

APP_NAME = os.environ.get("APP_NAME", "brightstar")
STAGE = os.environ.get("STAGE", "dev")

STUDENTS_TABLE = os.environ.get("STUDENTS_TABLE", "brightstar-dev-students")
COURSES_TABLE = os.environ.get("COURSES_TABLE", "brightstar-dev-courses")
ENROLLMENTS_TABLE = os.environ.get("ENROLLMENTS_TABLE", "brightstar-dev-enrollments")
GROUPS_TABLE = os.environ.get("GROUPS_TABLE", "brightstar-dev-groups")
RESULTS_TABLE = os.environ.get("RESULTS_TABLE", "brightstar-dev-results")
KNOWLEDGE_TABLE = os.environ.get("KNOWLEDGE_TABLE", "brightstar-dev-knowledge")
ENROLLMENTS_GSI1 = os.environ.get("ENROLLMENTS_GSI1", "GSI1")

# 退课截止：开课前 N 小时（可配置常量）
CANCEL_DEADLINE_HOURS = int(os.environ.get("CANCEL_DEADLINE_HOURS", "2"))

# 网页登录码有效期（天）；过期后需在微信重新发「登录码」获取
LOGIN_CODE_TTL_DAYS = int(os.environ.get("LOGIN_CODE_TTL_DAYS", "7"))

# 企业微信
WECOM_CORP_ID = os.environ.get("WECOM_CORP_ID", "")
WECOM_TOKEN = os.environ.get("WECOM_TOKEN", "")
WECOM_AES_KEY = os.environ.get("WECOM_AES_KEY", "")  # EncodingAESKey(43 位)
WECOM_AGENT_ID = os.environ.get("WECOM_AGENT_ID", "")
# 微信客服(kf)：通过中转服务器调企业微信 API（中转IP在可信IP白名单内）
WECOM_SECRET = os.environ.get("WECOM_SECRET", "")
WECOM_RELAY_URL = os.environ.get("WECOM_RELAY_URL", "")  # 形如 http://47.85.165.247:5005 或 https://xxx.ts.net
WECOM_RELAY_AUTH = os.environ.get("WECOM_RELAY_AUTH", "")  # 中转共享密钥(X-Relay-Auth);公网暴露时防滥用
WECOM_KF_OPEN_KFID = os.environ.get("WECOM_KF_OPEN_KFID", "")  # 主动发提醒用

# LINE Messaging API（前端适配层；后端业务与企业微信共用）
# 凭证优先走 SSM SecureString（不入 git）；未配置 SSM 时回退到环境变量明文。
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")          # 验签（回退用）
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")  # 回复/推送（回退用）
LINE_SECRET_PARAM = os.environ.get("LINE_SECRET_PARAM", "")             # SSM 参数名（优先）
LINE_TOKEN_PARAM = os.environ.get("LINE_TOKEN_PARAM", "")
REGION = os.environ.get("AWS_REGION", "ap-northeast-1")

_ssm = None
_ssm_cache = {}


def _get_ssm(name):
    global _ssm
    if name in _ssm_cache:
        return _ssm_cache[name]
    if _ssm is None:
        import boto3
        _ssm = boto3.client("ssm", region_name=REGION)
    val = _ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    _ssm_cache[name] = val
    return val


def line_secret():
    return _get_ssm(LINE_SECRET_PARAM) if LINE_SECRET_PARAM else LINE_CHANNEL_SECRET


def line_token():
    return _get_ssm(LINE_TOKEN_PARAM) if LINE_TOKEN_PARAM else LINE_CHANNEL_ACCESS_TOKEN

# 老师自助认证口令（学员发「老师认证 <口令>」即可升级为老师）
TEACHER_SIGNUP_CODE = os.environ.get("TEACHER_SIGNUP_CODE", "")

# 老师 userid 白名单（逗号分隔；企业微信成员 UserId）
TEACHER_OPENIDS = [
    o.strip() for o in os.environ.get("TEACHER_OPENIDS", "").split(",") if o.strip()
]

# Bedrock 意图解析
BEDROCK_ENABLED = os.environ.get("BEDROCK_ENABLED", "true").lower() == "true"
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)

# 自建 RAG（人工智能问答；仅学员开启 AI 模式后调用）
# 生成默认用日本境内 inference profile（数据不出日本），向量用 Titan v2
BEDROCK_CHAT_MODEL_ID = os.environ.get(
    "BEDROCK_CHAT_MODEL_ID", "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
)
BEDROCK_EMBED_MODEL_ID = os.environ.get(
    "BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0"
)
BEDROCK_EMBED_DIM = int(os.environ.get("BEDROCK_EMBED_DIM", "256"))

# Zoom
ZOOM_ENABLED = os.environ.get("ZOOM_ENABLED", "false").lower() == "true"
ZOOM_SECRET_NAME = os.environ.get("ZOOM_SECRET_NAME", f"{APP_NAME}/{STAGE}/zoom")
ZOOM_USER_ID = os.environ.get("ZOOM_USER_ID", "me")

# 时区：存储 UTC，展示 JST
TIMEZONE = "Asia/Tokyo"
