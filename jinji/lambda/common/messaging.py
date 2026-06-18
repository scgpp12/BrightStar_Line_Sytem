"""统一发送出口。当前只有 LINE 一个渠道；保留此层便于将来多渠道扩展。"""
from . import line


def send(user_id, text):
    return line.push(user_id, text)
