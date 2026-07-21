# -*- coding: utf-8 -*-
"""4 channel の LINE Rich Menu を生成・登録・既定設定する。

ローカルで PIL によりボタン画像を作り、LINE Messaging API で richmenu を作成→画像
アップロード→既定(default)に設定する。各 channel の access token は SSM から取得。
ボタンが送るテキストは各 webhook が解釈する文言（認証/研修/人事/未提出確認 等）。

実行： python tools/richmenu.py            # 全 channel
       python tools/richmenu.py shain     # 指定 channel のみ
"""
import io
import json
import sys
import urllib.request

import boto3
from PIL import Image, ImageDraw, ImageFont

ssm = boto3.client("ssm", region_name="ap-northeast-1")

FONT = r"C:\Windows\Fonts\meiryo.ttc"
NAVY = (14, 34, 56)        # 背景（濃紺）
TEAL = (22, 150, 180)      # 主要アクション（提出/認証）
GOLD = (202, 138, 4)       # 強調（その他経費/催促）
SLATE = (43, 74, 110)      # 通常ボタン（背景より明るい紺で視認可）
WHITE = (245, 245, 245)

# channel ごと：token パラメータ / グリッド(行,列) / ボタン[(表示, 送信テキスト, 色)]
CH = {
    "shain": {
        "token": "/brightstar-shain/dev/line/token",
        "rows": 2, "cols": 3, "h": 1000, "bar": "メニュー",
        "btns": [("勤怠提出", "勤怠提出", TEAL), ("経費提出", "経費提出", TEAL),
                 ("その他経費", "その他経費", GOLD),
                 ("履歴", "履歴", SLATE), ("研修", "研修", SLATE),
                 ("人事", "人事", SLATE)],
    },
    "soumu": {
        "token": "/brightstar-soumu/dev/line/token",
        "rows": 2, "cols": 4, "h": 1000, "bar": "総務メニュー",
        "btns": [("本日認証", "認証", TEAL), ("未提出確認", "未提出確認", SLATE),
                 ("催促", "催促", GOLD), ("催促予約", "催促予約", GOLD),
                 ("一覧", "一覧", SLATE), ("一括DL", "一括DL", SLATE),
                 ("一斉送信", "一斉送信", TEAL), ("ヘルプ", "ヘルプ", SLATE)],
    },
    "jinji": {
        "token": "/brightstar-hr/dev/line/token",
        "rows": 2, "cols": 3, "h": 1000, "bar": "人事メニュー",
        "btns": [("本日認証", "認証", TEAL), ("未提出確認", "未提出確認", SLATE),
                 ("一覧", "一覧", SLATE), ("一括DL", "一括DL", SLATE),
                 ("メール校正", "メール校正", GOLD), ("名簿", "名簿", SLATE)],
    },
    "kenshu": {
        "token": "/brightstar-kenshu/dev/line/token",
        "rows": 1, "cols": 3, "h": 520, "bar": "講師メニュー",
        "btns": [("本日認証", "認証", TEAL), ("講師ヘルプ", "老师帮助", SLATE),
                 ("学員一覧", "学员列表", SLATE)],
    },
    "eigyo": {
        "token": "/eki-commute/dev/line/channel-access-token",
        "rows": 1, "cols": 2, "h": 520, "bar": "営業メニュー",
        "btns": [("本日認証", "認証", TEAL),
                 ("要員リストDL", "要員一覧DL", GOLD)],
    },
}

W = 2500


def _font(sz):
    return ImageFont.truetype(FONT, sz, index=0)


def _draw_center(d, box, text, fill, sz):
    f = _font(sz)
    x0, y0, x1, y1 = box
    lines = text.split("\n")
    th = sum((d.textbbox((0, 0), ln, font=f)[3] - d.textbbox((0, 0), ln, font=f)[1]) + 12 for ln in lines)
    cy = (y0 + y1) / 2 - th / 2
    for ln in lines:
        bb = d.textbbox((0, 0), ln, font=f)
        w = bb[2] - bb[0]
        h = bb[3] - bb[1]
        d.text(((x0 + x1) / 2 - w / 2, cy - bb[1]), ln, font=f, fill=fill)
        cy += h + 12


ROWH = 220          # 1 行の高さ（コンパクト）
PAD_X, PAD_Y = 64, 30
BTN_FONT = 44


def _measure(d, text, f):
    lines = text.split("\n")
    w = max(d.textbbox((0, 0), ln, font=f)[2] - d.textbbox((0, 0), ln, font=f)[0] for ln in lines)
    h = sum((d.textbbox((0, 0), ln, font=f)[3] - d.textbbox((0, 0), ln, font=f)[1]) + 10 for ln in lines)
    return w, h


def _fit_font(d, text, maxw, maxh, start=130):
    """カード幅に収まる最大フォントを選ぶ（大きめ）。"""
    sz = start
    while sz >= 44:
        f = _font(sz)
        bb = d.textbbox((0, 0), text, font=f)
        if (bb[2] - bb[0]) <= maxw and (bb[3] - bb[1]) <= maxh:
            return f
        sz -= 6
    return _font(44)


def build(cfg):
    """セル全体を大きな角丸カードで塗り、中央に大きな文字（タップ領域＝セル全体）。"""
    rows, cols = cfg["rows"], cfg["cols"]
    H = cfg.get("h") or max(500, rows * 460)
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    cw, ch = W // cols, H // rows
    gap = 22
    areas = []
    for i, (label, text, color) in enumerate(cfg["btns"]):
        r, c = divmod(i, cols)
        x, y = c * cw, r * ch
        box = (x + gap, y + gap, x + cw - gap, y + ch - gap)   # セルいっぱいのカード
        d.rounded_rectangle(box, radius=44, fill=color)
        bw, bh = box[2] - box[0], box[3] - box[1]
        f = _fit_font(d, label, bw - 64, bh - 64)
        bb = d.textbbox((0, 0), label, font=f)
        tx = box[0] + (bw - (bb[2] - bb[0])) / 2 - bb[0]
        ty = box[1] + (bh - (bb[3] - bb[1])) / 2 - bb[1]
        d.text((tx, ty), label, font=f, fill=WHITE)
        areas.append({
            "bounds": {"x": x, "y": y, "width": cw, "height": ch},
            "action": {"type": "message", "text": text},
        })
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), areas, H


def _api(token, url, data, ctype="application/json", host="api.line.me"):
    full = "https://%s%s" % (host, url)
    req = urllib.request.Request(full, data=data, method="POST")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def setup(name):
    cfg = CH[name]
    token = ssm.get_parameter(Name=cfg["token"], WithDecryption=True)["Parameter"]["Value"]
    png, areas, H = build(cfg)
    body = {
        "size": {"width": W, "height": H},
        "selected": True,
        "name": "%s-menu" % name,
        "chatBarText": cfg["bar"],
        "areas": areas,
    }
    res = _api(token, "/v2/bot/richmenu", json.dumps(body).encode("utf-8"))
    rid = json.loads(res)["richMenuId"]
    _api(token, "/v2/bot/richmenu/%s/content" % rid, png, ctype="image/png", host="api-data.line.me")
    _api(token, "/v2/bot/user/all/richmenu/%s" % rid, b"", ctype="application/json")
    print("[%s] richmenu set default: %s (%d buttons)" % (name, rid, len(areas)))
    return rid


if __name__ == "__main__":
    targets = sys.argv[1:] or list(CH.keys())
    for t in targets:
        try:
            setup(t)
        except urllib.error.HTTPError as e:
            print("[%s] ERROR %s: %s" % (t, e.code, e.read().decode("utf-8", "ignore")))
