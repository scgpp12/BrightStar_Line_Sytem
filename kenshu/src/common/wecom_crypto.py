"""企业微信回调消息加解密（WXBizMsgCrypt 算法），基于纯 Python AES。

参考企业微信「加解密方案」：
- AESKey = base64decode(EncodingAESKey + "=")  (32 字节)，IV = AESKey[:16]
- 明文打包：random(16) + pack(">I", msglen) + msg + receiveid，再按 32 字节 PKCS7 填充
- AES-256-CBC 加密 → base64
- msg_signature = sha1(sort([token, timestamp, nonce, encrypt]))
"""
import base64
import hashlib
import os
import struct

from . import config
from ._pyaes import AES

_BLOCK = 32


def _aes_key() -> bytes:
    return base64.b64decode(config.WECOM_AES_KEY + "=")


def msg_signature(timestamp: str, nonce: str, encrypt: str) -> str:
    arr = sorted([config.WECOM_TOKEN, timestamp or "", nonce or "", encrypt or ""])
    return hashlib.sha1("".join(arr).encode()).hexdigest()


def verify(signature: str, timestamp: str, nonce: str, encrypt: str) -> bool:
    return bool(signature) and msg_signature(timestamp, nonce, encrypt) == signature


def _pkcs7_pad(data: bytes) -> bytes:
    amount = _BLOCK - (len(data) % _BLOCK)
    if amount == 0:
        amount = _BLOCK
    return data + bytes([amount]) * amount


def _pkcs7_unpad(data: bytes) -> bytes:
    pad = data[-1]
    if pad < 1 or pad > _BLOCK:
        pad = 0
    return data[:-pad] if pad else data


def decrypt(encrypt_b64: str) -> tuple[str, str]:
    """返回 (明文消息, receiveid)。receiveid 应等于 CorpID。"""
    key = _aes_key()
    raw = base64.b64decode(encrypt_b64)
    plain = AES(key).cbc_decrypt(raw, key[:16])
    plain = _pkcs7_unpad(plain)
    content = plain[16:]  # 去掉 16 字节随机前缀
    msg_len = struct.unpack(">I", content[:4])[0]
    msg = content[4:4 + msg_len].decode("utf-8")
    receiveid = content[4 + msg_len:].decode("utf-8")
    return msg, receiveid


def encrypt(plaintext: str, receiveid: str = "") -> str:
    key = _aes_key()
    rid = (receiveid or config.WECOM_CORP_ID).encode()
    msg = plaintext.encode("utf-8")
    text = os.urandom(16) + struct.pack(">I", len(msg)) + msg + rid
    enc = AES(key).cbc_encrypt(_pkcs7_pad(text), key[:16])
    return base64.b64encode(enc).decode()
