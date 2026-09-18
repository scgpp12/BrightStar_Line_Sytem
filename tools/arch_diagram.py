# -*- coding: utf-8 -*-
"""BrightStar インフラアーキテクチャ図（.drawio）を生成する。

PNG は生成しない。.drawio を唯一の正とし、書き出しは drawio 本体に任せる：

    wsl -e bash -lc "cd /mnt/c/.../docs/architecture && \
      docker run --rm -v \"$PWD\":/data rlespinasse/drawio-export -f png --scale 2 <file>.drawio"

作図の約束（SP_インフラアーキテクチャ図 に合わせる）
  ・サービスは AWS4 公式アイコン（78x78・ラベルはアイコンの下）
  ・論理的なまとまりは mxgraph.aws4.group（AWS Cloud / Region / 破線グループ）
  ・線は orthogonalEdgeStyle + jumpStyle=arc。交差はアーチで飛び越す
  ・アイコンを貫通させないため、出入口（exitX/entryX）と waypoint を明示する
"""
import html
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "docs", "architecture")
FILE = "BS_インフラアーキテクチャ図.drawio"

# AWS カテゴリ色
C_COMPUTE = "#ED7100"
C_NET     = "#8C4FFF"
C_DB      = "#C925D1"
C_STORAGE = "#7AA116"
C_APPINT  = "#E7157B"
C_MGMT    = "#E7157B"
C_SEC     = "#DD344C"
INK       = "#232F3E"

cells = []
_seq = [0]


def _id(p="c"):
    _seq[0] += 1
    return "%s%d" % (p, _seq[0])


def esc(s):
    return html.escape(s or "", quote=True)


def raw(s):
    """drawio の value（html=1）に入れる文字列。

    属性内なので & " < > をエスケープし、改行は drawio が解釈する <br> に変換する
    （生の改行は描画時に潰れて 1 行になる）。"""
    return (s.replace("&", "&amp;").replace('"', "&quot;")
             .replace("<", "&lt;").replace(">", "&gt;")
             .replace("\n", "&lt;br&gt;"))


def group(x, y, w, h, label, stroke=INK, gr_icon=None, dashed=0, fill="none",
          nid=None, parent="1", font=13):
    nid = nid or _id("g")
    if gr_icon:
        style = ("points=[[0,0],[0.25,0],[0.5,0],[0.75,0],[1,0],[1,0.25],[1,0.5],[1,0.75],"
                 "[1,1],[0.75,1],[0.5,1],[0.25,1],[0,1],[0,0.75],[0,0.5],[0,0.25]];"
                 "outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=%d;"
                 "fontStyle=1;container=1;pointerEvents=0;collapsible=0;recursiveResize=0;"
                 "shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.%s;strokeColor=%s;fillColor=%s;"
                 "verticalAlign=top;align=left;spacingLeft=30;fontColor=%s;dashed=%d;"
                 % (font, gr_icon, stroke, fill, stroke, dashed))
    else:
        style = ("rounded=1;whiteSpace=wrap;html=1;fillColor=%s;strokeColor=%s;dashed=%d;"
                 "verticalAlign=top;align=left;spacingLeft=10;spacingTop=2;fontColor=%s;"
                 "fontSize=%d;fontStyle=1;container=1;collapsible=0;pointerEvents=0;"
                 % (fill, stroke, dashed, stroke, font))
    cells.append('<mxCell id="%s" value="%s" style="%s" vertex="1" parent="%s">'
                 '<mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/></mxCell>'
                 % (nid, esc(label), style, parent, x, y, w, h))
    return nid


def icon(x, y, label, res, color, nid=None, parent="1", tip="", size=78):
    """AWS4 公式アイコン。ラベルはアイコンの下に出る。"""
    nid = nid or _id("i")
    style = ("sketch=0;points=[[0,0,0],[0.25,0,0],[0.5,0,0],[0.75,0,0],[1,0,0],[0,1,0],"
             "[0.25,1,0],[0.5,1,0],[0.75,1,0],[1,1,0],[0,0.25,0],[0,0.5,0],[0,0.75,0],"
             "[1,0.25,0],[1,0.5,0],[1,0.75,0]];outlineConnect=0;fontColor=%s;"
             "gradientColor=none;fillColor=%s;strokeColor=none;dashed=0;"
             "verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;"
             "fontSize=11;fontStyle=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;"
             "resIcon=mxgraph.aws4.%s;" % (INK, color, res))
    body = ('<mxCell id="%s" value="%s" style="%s" vertex="1" parent="%s">'
            '<mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/></mxCell>'
            % (nid, raw(label), style, parent, x, y, size, size))
    if tip:
        cells.append('<UserObject label="%s" tooltip="%s" id="%s">%s</UserObject>'
                     % (raw(label), esc(tip), nid,
                        body.replace(' id="%s"' % nid, "").replace('value="%s" ' % raw(label), "")))
    else:
        cells.append(body)
    return nid


def box(x, y, w, h, label, stroke, nid=None, parent="1", fill="#FFFFFF", font=12,
        bold=1, align="center"):
    nid = nid or _id("b")
    cells.append('<mxCell id="%s" value="%s" style="rounded=1;whiteSpace=wrap;html=1;'
                 'fillColor=%s;strokeColor=%s;fontColor=%s;fontSize=%d;fontStyle=%d;'
                 'align=%s;verticalAlign=middle;" vertex="1" parent="%s">'
                 '<mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/></mxCell>'
                 % (nid, raw(label), fill, stroke, INK, font, bold, align, parent, x, y, w, h))
    return nid


def note(x, y, w, h, label, parent="1", font=11):
    nid = _id("n")
    cells.append('<mxCell id="%s" value="%s" style="shape=note;whiteSpace=wrap;html=1;size=16;'
                 'fillColor=#FFF8D5;strokeColor=#D6C36A;fontColor=#3A3218;fontSize=%d;'
                 'align=left;verticalAlign=top;spacingLeft=8;spacingTop=4;" vertex="1" parent="%s">'
                 '<mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/></mxCell>'
                 % (nid, raw(label), font, parent, x, y, w, h))
    return nid


def text(x, y, w, h, label, font=12, bold=0, color=INK, parent="1", align="left"):
    nid = _id("t")
    cells.append('<mxCell id="%s" value="%s" style="text;html=1;align=%s;verticalAlign=middle;'
                 'fontSize=%d;fontStyle=%d;fontColor=%s;" vertex="1" parent="%s">'
                 '<mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/></mxCell>'
                 % (nid, raw(label), align, font, bold, color, parent, x, y, w, h))
    return nid


def edge(src, dst, label="", color="#5A6B7F", dashed=0, points=None,
         exit_=None, entry=None, parent="1", font=11, width=1.5):
    """points: [(x,y), ...]（絶対座標）。exit_/entry: (x比, y比)。"""
    st = ("edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
          "jumpStyle=arc;jumpSize=8;strokeColor=%s;strokeWidth=%s;dashed=%d;fontSize=%d;"
          "fontColor=%s;labelBackgroundColor=#FFFFFF;endArrow=blockThin;endFill=1;"
          % (color, width, dashed, font, color))
    if exit_:
        st += "exitX=%s;exitY=%s;exitDx=0;exitDy=0;exitPerimeter=0;" % exit_
    if entry:
        st += "entryX=%s;entryY=%s;entryDx=0;entryDy=0;entryPerimeter=0;" % entry
    geo = '<mxGeometry relative="1" as="geometry">'
    if points:
        geo += "<Array as=\"points\">" + "".join(
            '<mxPoint x="%d" y="%d"/>' % (px, py) for px, py in points) + "</Array>"
    geo += "</mxGeometry>"
    cells.append('<mxCell id="%s" value="%s" style="%s" edge="1" parent="%s" source="%s" '
                 'target="%s">%s</mxCell>'
                 % (_id("e"), raw(label), st, parent, src, dst, geo))


# ══════════════════════════════════════════════════════════════════
# レイアウト
# ══════════════════════════════════════════════════════════════════
COL = [140, 360, 580, 800, 1020, 1240]          # チャネル6列（縦一直線）
IC = 78
CX = [c + IC // 2 for c in COL]                 # 各列のアイコン中心X

text(40, 24, 900, 24, "BrightStar 統合LINEアシスタント　インフラアーキテクチャ", 19, 1)
text(40, 48, 1100, 20,
     "正 = BS_INF-01〜10 設計書　／　ap-northeast-1 ・ アカウント 603319838936 ・ stage=dev", 12)
text(40, 68, 1200, 20,
     "実機照合 2026-09-19：Lambda 11本 ・ DynamoDB 15表 ・ S3 1本。デプロイ済みコードとリポジトリの md5 一致を確認済み",
     12, 0, "#1B7A3E")

# ── 利用者 ──────────────────────────────────────────────
group(100, 110, 1260, 130, "利用者（社内 約100名）／ 役割は社員名簿 roster の role で判定",
      "#5A6B7F", dashed=1, nid="g_users", font=12)
USERS = [("一般社員", "employee", "u_shain"), ("総務", "hr / soumu", "u_soumu"),
         ("人事", "hr", "u_jinji"), ("営業", "sales", "u_eigyo"),
         (None, None, "u_tool"), ("講師", "teacher", "u_kenshu")]
for i, (nm, role, nid) in enumerate(USERS):
    if nm:
        icon(COL[i], 140, "%s\n(%s)" % (nm, role), "users", INK, nid=nid, size=52)
# 社内ツールは LINE を経由しない。LINE Platform 枠の外（右側）に置く
icon(1450, 140, "社内ツール\n（機械クライアント）", "users", INK, nid="u_tool", size=52)

# ── LINE Platform ──────────────────────────────────────
group(100, 270, 1260, 110, "LINE Platform（同一 Provider ＝ userId が全チャネルで一致）",
      "#06C755", nid="g_line", font=12)
ACC = [("BS社員管理", "acc_shain"), ("BS総務", "acc_soumu"), ("BS人事", "acc_jinji"),
       ("BS営業", "acc_eigyo"), (None, None), ("BS研修", "acc_kenshu")]
for i, (nm, nid) in enumerate(ACC):
    if nm:
        box(COL[i] - 22, 305, 122, 52, nm + "\n公式アカウント", "#06C755", nid=nid, font=11)

# ── AWS Cloud ──────────────────────────────────────────
group(60, 410, 2000, 900, "AWS Cloud", INK, gr_icon="group_aws_cloud_alt", nid="g_aws")
group(80, 450, 1960, 845, "ap-northeast-1", "#147EBA", gr_icon="group_region", nid="g_region")

# 入口層
box(100, 500, 1100, 44, "Lambda Function URL　authType = NONE（LINE は SigV4 を付けられないため）",
    C_NET, nid="ep_furl", fill="#F4EEFF", font=11)
icon(COL[5], 490, "API Gateway\nHTTP API（研修・7ルート）", "api_gateway", C_NET, nid="ep_apigw")

# Lambda 層
LAM = [("shain-webhook\n512MB / 29s", "l_shain"), ("soumu-webhook\n512MB / 29s", "l_soumu"),
       ("hr-webhook\n256MB / 29s", "l_hr"), ("eigyo LineWebhookFn\n256MB / 300s", "l_eigyo"),
       ("eigyo ApiFn\n256MB / 300s", "l_eigyoapi"),
       ("kenshu webhook ×3\n1024MB / 20s", "l_kenshu")]
for i, (nm, nid) in enumerate(LAM):
    icon(COL[i], 620, nm, "lambda", C_COMPUTE, nid=nid)
icon(COL[1], 800, "soumu-reminder\n催促 / 予約 / 一斉送信", "lambda", C_COMPUTE, nid="l_rem")
icon(COL[2], 800, "hr-reconcile\n日次点検 120s", "lambda", C_COMPUTE, nid="l_rec")
icon(COL[5], 800, "kenshu Reminder\n開講1h前", "lambda", C_COMPUTE, nid="l_krem")

text(100, 556, 400, 32,
     "Lambda：Python 3.12 / arm64（営業のみ x86_64）\n外部ライブラリなし（標準ライブラリ + boto3）",
     10, 1, "#7A4A00")

# データ層
group(100, 970, 700, 300, "共有データ　BrightstarHr-dev が所有（他4スタックは名前で参照）",
      C_DB, dashed=1, nid="g_shared", font=12)
for i, (nm, nid) in enumerate([("roster\n社員名簿・PITR有", "t_roster"),
                               ("auth\n日次認証・TTL", "t_auth"),
                               ("employees\n紐付け・PITR有", "t_emp"),
                               ("submissions\n提出記録・GSI1", "t_sub")]):
    icon(130 + i * 170, 1005, nm, "dynamodb", C_DB, nid=nid)
icon(130, 1150, "S3　提出物・テンプレート", "s3", C_STORAGE, nid="s3")
text(228, 1152, 540, 96,
     "brightstar-hr-dev-{account}\n"
     "hr/{年}/{月}/{worktimes|expenses|others}　hr/template ・ pending ・ exports\n"
     "公開遮断 / SSE-S3 / バージョニング有\n"
     "60日 → Deep Archive　365日削除", 10)

group(830, 970, 500, 300, "チャネル固有データ", C_DB, dashed=1, nid="g_own", font=12)
for i, (nm, nid) in enumerate([("session\n社員・当日モード", "t_sess"),
                               ("bookings\n催促予約", "t_book"),
                               ("broadcasts\n配信・既読確認", "t_bc")]):
    icon(860 + i * 155, 1005, nm, "dynamodb", C_DB, nid=nid)
icon(860, 1150, "kenshu 6表", "dynamodb", C_DB, nid="t_kenshu")
icon(1060, 1150, "EkiCommute 2表", "dynamodb", C_DB, nid="t_eki")
text(860, 1235, 460, 30,
     "全15表 ＝ PAY_PER_REQUEST　暗号化 ＝ AWS管理キー（CMK 不採用）", 10)

# 右カラム：定期実行 / 機密 / 監視
group(1380, 490, 300, 420, "定期実行", C_APPINT, dashed=1, nid="g_evb", font=12)
EVB = [("reminder-schedule\n毎月25/28日 9:00 JST", "e_rem"),
       ("booking-poller\n10分間隔", "e_poll"),
       ("reconcile-schedule\n毎日 0:00 JST", "e_rec"),
       ("kenshu ReminderTick\n10分間隔", "e_krem")]
for i, (nm, nid) in enumerate(EVB):
    icon(1420, 530 + i * 95, nm, "eventbridge", C_APPINT, nid=nid, size=56)

group(1720, 490, 300, 200, "機密情報", C_SEC, dashed=1, nid="g_sec", font=12)
icon(1760, 530, "SSM Parameter Store\nSecureString ×10", "systems_manager", C_SEC,
     nid="ssm", size=56)
text(1740, 622, 275, 44,
     "全 Lambda が起動時に取得（線は省略）\n/{app}/dev/line/{secret,token} ×5チャネル", 10)

group(1720, 720, 300, 190, "監視", C_MGMT, dashed=1, nid="g_mon", font=12)
icon(1760, 758, "CloudWatch\nLogs 30日 / アラーム10", "cloudwatch_2", C_MGMT, nid="cw", size=56)
icon(1900, 758, "SNS\nbrightstar-ops-dev-alerts", "sns", C_APPINT, nid="sns", size=56)

# ── 外部サービス（AWS 外） ────────────────────────────
group(2100, 410, 420, 900, "外部サービス（AWS 外・すべて HTTPS）", "#B8860B", dashed=1,
      nid="g_ext", font=12)
EXT = [("企業微信 / WeChat\n研修のみ・API GW /wechat", "x_wecom"),
       ("Zoom API\n研修の開講リンク発行", "x_zoom"),
       ("駅探（ekitan）\n営業・通勤経路の取得", "x_eki"),
       ("sons02 メール校正\n人事からリンク誘導のみ", "x_mail")]
for i, (nm, nid) in enumerate(EXT):
    box(2130, 460 + i * 90, 360, 60, nm, "#B8860B", nid=nid, fill="#FFFBEF", font=11)
icon(2130, 830, "Amazon Location\ngeo-places（住所→最寄駅）", "location_service", C_NET,
     nid="x_loc", size=56)

note(2130, 960, 360, 330,
     "外部依存の扱い\n"
     "・駅探は HTML 構造の変更で壊れる想定。\n"
     "　データ源の差し替えを契約事項としている\n"
     "・Bedrock は権限のみ付与・現在未使用\n"
     "　（東京では inference-profile が必須）\n"
     "・企業微信の認証情報は現在 Lambda 環境変数に\n"
     "　平文。SSM SecureString 化が必要\n"
     "　（BS_INF-08 §4）\n\n"
     "個人情報の所在\n"
     "氏名・社員番号・所属・勤務実績・通勤経路\n"
     "マイナンバー / 口座 / 在留カードは扱わない\n"
     "横断閲覧（一覧・CSV・一括DL・削除）は\n"
     "役割 hr / soumu に限定（BS_INF-07 §3）")

# ══════════════════════════════════════════════════════════════════
# フロー（アイコンを貫通させないよう waypoint で車線を作る）
#   車線 Y=960（Lambda とデータ層の間）、車線 X=1345（右カラムへの縦道）
# ══════════════════════════════════════════════════════════════════
BLUE, GREEN, ORANGE, PINK, GRAY = "#1565C0", "#2E7D32", "#E65100", "#AD1457", "#5A6B7F"

# 利用者 → LINE → 入口 → Lambda（すべて真下。交差なし）
for i, (_, _, unid) in enumerate(USERS):
    if unid == "u_tool":
        continue
    a = ACC[i][1]
    edge(unid, a, "", GRAY, exit_=("0.5", "1"), entry=("0.5", "0"))
for i, (_, nid) in enumerate(ACC):
    if not nid:
        continue
    tgt = "ep_apigw" if i == 5 else "ep_furl"
    if i == 5:
        edge(nid, tgt, "", GRAY, exit_=("0.5", "1"), entry=("0.5", "0"))
    else:
        edge(nid, tgt, "", GRAY, exit_=("0.5", "1"),
             entry=("%.3f" % ((CX[i] - 100) / 1100.0), "0"))
for i in range(5):
    edge("ep_furl", LAM[i][1], "", GRAY,
         exit_=("%.3f" % ((CX[i] - 100) / 1100.0), "1"), entry=("0.5", "0"))
edge("ep_apigw", "l_kenshu", "", GRAY, exit_=("0.5", "1"), entry=("0.5", "0"))
edge("u_tool", "ep_furl", "SigV4", GRAY,
     exit_=("0.5", "1"), entry=("1", "0.5"),
     points=[(1476, 470), (1240, 470), (1240, 522)])

# ① 本人確認：shain → roster
edge("l_shain", "t_roster", "①", BLUE, exit_=("0.5", "1"), entry=("0.5", "0"),
     points=[(CX[0], 960), (169, 960)])
# ② 提出：shain → S3（左の外側を通してアイコンを避ける）
edge("l_shain", "s3", "②", GREEN, exit_=("0", "0.5"), entry=("0", "0.5"),
     points=[(92, 659), (92, 1189)])
# ③ 回収：soumu → submissions
edge("l_soumu", "t_sub", "③", BLUE, exit_=("0.5", "1"), entry=("0.5", "0"),
     points=[(CX[1], 960), (679, 960)])
# ⑥ DL：soumu → S3
edge("l_soumu", "s3", "⑥", GREEN, exit_=("0", "0.5"), entry=("1", "0.5"),
     points=[(340, 659), (340, 1189)])
# ⑤ 既読確認：reminder → broadcasts
edge("l_rem", "t_bc", "⑤", PINK, exit_=("1", "0.5"), entry=("0.5", "0"),
     points=[(830, 839), (830, 950), (1209, 950)])
# ④ push は社員チャネルの token で送る（左端を大きく迂回してアイコンを避ける）
edge("l_rem", "acc_shain", "④", PINK, exit_=("0", "0.5"), entry=("0", "0.5"),
     points=[(70, 839), (70, 331)])
# ④⑦⑧ EventBridge → Lambda
#   縦道 x=1345/1355/1365、横車線 y=906/920/934（Lambda 下端878 とデータ層上端970 の間）
edge("e_rem", "l_rem", "④", ORANGE, exit_=("0", "0.5"), entry=("0.25", "1"),
     points=[(1345, 558), (1345, 906), (380, 906)])
edge("e_poll", "l_rem", "⑦", ORANGE, exit_=("0", "0.5"), entry=("0.75", "1"),
     points=[(1355, 653), (1355, 920), (418, 920)])
edge("e_rec", "l_rec", "⑧", ORANGE, exit_=("0", "0.5"), entry=("0.5", "1"),
     points=[(1365, 748), (1365, 934), (619, 934)])
edge("e_krem", "l_krem", "", ORANGE, exit_=("0", "0.5"), entry=("1", "0.5"))
# 非同期 Invoke
edge("l_soumu", "l_rem", "非同期", GRAY, dashed=1, exit_=("0.5", "1"), entry=("0.5", "0"))
# ⑧ 紐付け解除
edge("l_rec", "t_roster", "⑧", ORANGE, exit_=("0.5", "1"), entry=("1", "0.5"),
     points=[(CX[2], 945), (300, 945), (300, 1044)])
# 参照（破線）
# SSM は全 Lambda が起動時に読むため、線は引かず 機密情報 グループ内に注記する
edge("l_shain", "t_sess", "", GRAY, dashed=1, exit_=("1", "0.5"), entry=("0", "0.5"),
     points=[(240, 900), (240, 1044)])
edge("l_rem", "t_book", "", GRAY, dashed=1, exit_=("0.5", "1"), entry=("0.5", "0"))
edge("l_kenshu", "t_kenshu", "", GRAY, dashed=1, exit_=("0", "0.5"), entry=("1", "0.5"),
     points=[(1340, 1189)])
edge("l_eigyo", "t_eki", "", GRAY, dashed=1, exit_=("0.5", "1"), entry=("0.5", "0"),
     points=[(CX[3], 1120), (1099, 1120)])
edge("cw", "sns", "", GRAY, exit_=("1", "0.5"), entry=("0", "0.5"))
# 外部サービス
edge("l_kenshu", "x_wecom", "", GRAY, exit_=("1", "0.5"), entry=("0", "0.5"),
     points=[(2060, 659), (2060, 490)])
edge("l_kenshu", "x_zoom", "", GRAY, exit_=("1", "0.5"), entry=("0", "0.5"),
     points=[(2050, 659), (2050, 580)])
edge("l_eigyo", "x_eki", "", GRAY, exit_=("1", "0.5"), entry=("0", "0.5"),
     points=[(1320, 690), (1320, 960), (2070, 960), (2070, 670)])
edge("l_eigyo", "x_loc", "", GRAY, exit_=("1", "0.5"), entry=("0", "0.5"),
     points=[(1330, 700), (1330, 970), (2085, 970), (2085, 858)])
edge("l_hr", "x_mail", "", GRAY, dashed=1, exit_=("1", "0.5"), entry=("0", "0.5"),
     points=[(1310, 680), (1310, 940), (2095, 940), (2095, 760)])

# ══════════════════════════════════════════════════════════════════
# 凡例・注記
# ══════════════════════════════════════════════════════════════════
group(60, 1350, 620, 180, "凡例（線の色）", "#5A6B7F", dashed=1, font=12)
LEG = [(BLUE, "① ③ 同期リクエスト（本人確認・回収）"),
       (GREEN, "② ⑥ S3 経路（提出・ダウンロード）"),
       (ORANGE, "④ ⑦ ⑧ EventBridge 起動（定期実行）"),
       (PINK, "⑤ 既読確認 ／ push 配信"),
       (GRAY, "経路（実線）／ 非同期 Invoke・参照（破線）")]
for i, (c, s) in enumerate(LEG):
    text(80, 1388 + i * 28, 60, 20, "━━━", 14, 1, c)
    text(150, 1388 + i * 28, 520, 20, s, 11)

note(710, 1350, 720, 330,
     "処理フロー（線上の番号）\n"
     "① 本人確認：「所属部署 お名前」→ roster 照合 → auth に当日認証（TTL＝当日限り）\n"
     "② 提出：Excel / PDF / 画像 → S3 に保存し submissions に記録\n"
     "③ 回収：総務が submissions の GSI1（月×種別）を Query して横断集計\n"
     "④ 催促：EventBridge（25/28日）→ reminder →「社員チャネルの token」で push\n"
     "⑤ 既読確認：社員が postback → broadcasts に記録（配信IDは画面に出さない）\n"
     "⑥ ダウンロード：ZIP/CSV を exports/ に生成 → 署名付き短縮URL → presigned で取得\n"
     "⑦ 予約催促：bookings を10分間隔のポーラーが拾い、実行時点の未提出者のみへ送信\n"
     "⑧ 日次点検：毎日0:00 に到達性を確認し、到達不可なら紐付けを解除\n\n"
     "なぜ authType = NONE か\n"
     "LINE Platform は Webhook に SigV4 を付けられず、AWS_IAM にすると全リクエストが403。\n"
     "AWS 層は通し、アプリ層で HMAC-SHA256 の署名検証を必ず行う（BS_INF-05 §3）。\n"
     "→ チャネルシークレットの漏洩が単一障害点。SSM SecureString にのみ保持する。")

note(1460, 1350, 620, 330,
     "早期対応を推奨する課題（BS_INF 横断）\n"
     "① 企業微信の認証情報が Lambda 環境変数に平文　　　　【高】BS_INF-08 §4\n"
     "② 全 DynamoDB / S3 が RemovalPolicy.DESTROY。\n"
     "　 人事スタック削除で全社データ消失　　　　　　　　　【高】BS_INF-03 §7\n"
     "③ /dl の署名に有効期限が無い（露出は最大3日）　　　　【中】BS_INF-04 §6\n"
     "④ 60日超の月は Deep Archive で一括DLが失敗　　　　　【中】BS_INF-04 §5\n"
     "⑤ IAM が粗い（読み取りのみの関数にも DeleteItem）　　【中】BS_INF-07 §5\n"
     "⑥ ログに氏名・社員番号が出力される　　　　　　　　　【中】BS_INF-09 §3\n\n"
     "※ 脆弱性検査（SAST / DAST）・ペネトレーションテストは未実施。\n"
     "　 実施済みは機能面の単体・結合テストのみ。\n\n"
     "対応済（2026-09-19）：ログ保持期間 30日化・孤立ロググループ5件削除・\n"
     "CloudWatch アラーム10件と SNS 通知の新設・デプロイ同期の機械検証。")

note(2100, 1350, 460, 330,
     "所有区分（スタック依存）\n"
     "・BrightstarHr-dev（人事）が roster / auth /\n"
     "　employees / submissions / S3 / SNS を所有。\n"
     "　他4スタックは名前・ARN 文字列で参照する。\n"
     "・初回デプロイは人事を最初に実行する。\n"
     "・人事スタックの削除は他4チャネルを巻き込む。\n"
     "・各チャネルは独立スタック＝\n"
     "　障害・改修が他チャネルへ波及しない。\n\n"
     "スタック一覧\n"
     "brightstar-kenshu-dev／BrightstarHr-dev／\n"
     "BrightstarSoumu-dev／brightstar-shain-dev／\n"
     "EkiCommute-dev")

# ══════════════════════════════════════════════════════════════════
xml = ('<mxfile host="app.diagrams.net" agent="brightstar-tools">'
       '<diagram name="BrightStar インフラ" id="bs-infra">'
       '<mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" '
       'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="2600" '
       'pageHeight="1700" math="0" shadow="0"><root>'
       '<mxCell id="0"/><mxCell id="1" parent="0"/>'
       + "".join(cells) +
       "</root></mxGraphModel></diagram></mxfile>")

os.makedirs(OUT, exist_ok=True)
path = os.path.join(OUT, FILE)
with open(path, "w", encoding="utf-8") as f:
    f.write(xml)

import xml.etree.ElementTree as ET
ET.fromstring(xml)
print("drawio OK :", os.path.abspath(path))
print("cells     :", len(cells))
