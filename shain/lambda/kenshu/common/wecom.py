"""企业微信自建应用：回调消息解析 + 被动加密回复封装。

GET  验签：?msg_signature&timestamp&nonce&echostr → 验签 + 解密 echostr → 回明文
POST 消息：body 是 <xml><Encrypt>..</Encrypt></xml> → 验签(Encrypt) → 解密内层 XML → 处理
         → 把回复明文 XML 加密 → 外层封装 <Encrypt>/<MsgSignature>/<TimeStamp>/<Nonce>
"""
import os
import time
import xml.etree.ElementTree as ET

from . import wecom_crypto


def extract_encrypt(body_xml: str) -> str:
    root = ET.fromstring(body_xml)
    el = root.find("Encrypt")
    return el.text if el is not None else ""


def parse_message(plain_xml: str) -> dict:
    root = ET.fromstring(plain_xml)

    def g(tag):
        el = root.find(tag)
        return el.text if el is not None and el.text is not None else ""

    return {
        "fromUser": g("FromUserName"),   # 企业成员 UserId（自建应用消息时）
        "toUser": g("ToUserName"),       # CorpID
        "msgType": g("MsgType"),
        "content": (g("Content") or "").strip(),
        "event": g("Event"),
        "eventKey": g("EventKey"),
        "agentId": g("AgentID"),
        "kfToken": g("Token"),           # 微信客服事件：拉取消息用的 token
        "openKfId": g("OpenKfId"),       # 微信客服账号 id
    }


def _passive_plain(to_user: str, from_corp: str, content: str) -> str:
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_corp}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "</xml>"
    )


def build_encrypted_reply(to_user: str, from_corp: str, content: str) -> str:
    """构造被动回复（加密）整包 XML。"""
    plain = _passive_plain(to_user, from_corp, content)
    encrypt = wecom_crypto.encrypt(plain, from_corp)
    ts = str(int(time.time()))
    nonce = os.urandom(8).hex()
    sig = wecom_crypto.msg_signature(ts, nonce, encrypt)
    return (
        "<xml>"
        f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
        f"<MsgSignature><![CDATA[{sig}]]></MsgSignature>"
        f"<TimeStamp>{ts}</TimeStamp>"
        f"<Nonce><![CDATA[{nonce}]]></Nonce>"
        "</xml>"
    )


def http(status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> dict:
    return {"statusCode": status, "headers": {"Content-Type": content_type}, "body": body}


def xml_resp(body: str) -> dict:
    return http(200, body, "application/xml; charset=utf-8")
