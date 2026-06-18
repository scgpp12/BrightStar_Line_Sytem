"""统一发送层：按内部 openid 前缀选择平台渠道，业务/提醒代码无需关心平台。

  - `line:` 前缀 → LINE push
  - 其它          → 企业微信客服 kf.send_text
返回统一含 errcode 的 dict（errcode==0 视为成功）。
"""
from . import config, kf, line


def send(openid: str, text: str) -> dict:
    if openid.startswith(line.USER_PREFIX):
        return line.push(openid[len(line.USER_PREFIX):], text)
    return kf.send_text(config.WECOM_KF_OPEN_KFID, openid, text)
