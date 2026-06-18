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
NAVY = (26, 41, 66)
TEAL = (15, 118, 110)
GOLD = (202, 138, 4)
WHITE = (245, 245, 245)

# channel ごと：token パラメータ / グリッド(行,列) / ボタン[(表示, 送信テキスト, 色)]
CH = {
    "shain": {
        "token": "/brightstar-shain/dev/line/token",
        "rows": 1, "cols": 2, "h": 843, "bar": "メニュー",
        "btns": [("📚 研修\n受講・申込", "研修", TEAL), ("🗂️ 人事\n勤怠・通勤費", "人事", GOLD)],
    },
    "jinji": {
        "token": "/brightstar-hr/dev/line/token",
        "rows": 2, "cols": 3, "h": 1686, "bar": "人事メニュー",
        "btns": [("✅ 本日認証", "認証", TEAL), ("未提出確認", "未提出確認", NAVY),
                 ("一覧", "一覧", NAVY), ("一括DL", "一括DL", NAVY),
                 ("メール校正", "メール校正", GOLD), ("名簿", "名簿", NAVY)],
    },
    "kenshu": {
        "token": "/brightstar-kenshu/dev/line/token",
        "rows": 1, "cols": 3, "h": 843, "bar": "講師メニュー",
        "btns": [("✅ 本日認証", "認証", TEAL), ("講師ヘルプ", "老师帮助", NAVY),
                 ("学員一覧", "学员列表", NAVY)],
    },
    "eigyo": {
        "token": "/eki-commute/dev/line/channel-access-token",
        "rows": 1, "cols": 2, "h": 843, "bar": "営業メニュー",
        "btns": [("✅ 本日認証\n(後で現場駅名)", "認証", TEAL),
                 ("📋 要員リストDL\n(編集→送り返す)", "要員一覧DL", GOLD)],
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


def build(cfg):
    """ボタンは文字サイズ＋余白の「ピル」だけ塗る（タップ領域はセル全体）。全体も低め。"""
    rows, cols = cfg["rows"], cfg["cols"]
    H = max(260, rows * ROWH)
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    cw, ch = W // cols, H // rows
    f = _font(BTN_FONT)
    areas = []
    for i, (label, text, color) in enumerate(cfg["btns"]):
        r, c = divmod(i, cols)
        x, y = c * cw, r * ch
        cx, cy = x + cw / 2, y + ch / 2
        tw, th = _measure(d, label, f)
        pw = min(cw - 36, tw + 2 * PAD_X)          # 文字幅＋余白（セル幅で頭打ち）
        ph = min(ch - 30, th + 2 * PAD_Y)
        box = (cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2)
        d.rounded_rectangle(box, radius=ph / 2, fill=color)   # ピル（両端まる）
        _draw_center(d, box, label, WHITE, BTN_FONT)
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
