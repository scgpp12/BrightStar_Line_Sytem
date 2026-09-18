# -*- coding: utf-8 -*-
"""BrightStar インフラアーキテクチャ図を生成する。

レイアウトを 1 箇所のデータとして持ち、そこから
  ・SVG（→ Chrome で PNG 化）
  ・.drawio（AWS4 公式アイコン付き・編集可能）
の両方を出力する。両者が食い違わないようにするための構成。
"""
import html
import os

OUT = r"C:\Users\sons\Downloads\aws-test\BrightStar_Line_System\docs\architecture"
W, H = 2640, 1780
NL = "\n"

# ---- 配色（AWS カテゴリ色） ----
BG      = "#0E1520"
PANEL   = "#16202E"
LINE_GR = "#06C755"      # LINE ブランド
C_COMP  = "#ED7100"      # Compute（Lambda）
C_STOR  = "#7AA116"      # Storage（S3）
C_DB    = "#C925D1"      # Database（DynamoDB）
C_NET   = "#8C4FFF"      # Networking（API GW / Function URL / Location）
C_APP   = "#E7157B"      # App Integration / Management
C_SEC   = "#DD344C"      # Security（SSM / KMS）
C_ML    = "#01A88D"      # AI/ML（Bedrock）
TXT     = "#E6EDF6"
MUTE    = "#8FA3BF"
NOTE_BG = "#FFF6C6"
NOTE_FG = "#2B2B1A"

RES = {
    "lambda": "mxgraph.aws4.lambda", "apigw": "mxgraph.aws4.api_gateway",
    "ddb": "mxgraph.aws4.dynamodb", "s3": "mxgraph.aws4.s3",
    "evb": "mxgraph.aws4.eventbridge", "ssm": "mxgraph.aws4.systems_manager",
    "cw": "mxgraph.aws4.cloudwatch_2", "bedrock": "mxgraph.aws4.sagemaker",
    "loc": "mxgraph.aws4.location_service", "user": "mxgraph.aws4.users",
}

groups, nodes, edges, notes, texts = [], [], [], [], []


def G(x, y, w, h, label, color=MUTE, dash=True):
    groups.append(dict(x=x, y=y, w=w, h=h, label=label, color=color, dash=dash))


def N(x, y, w, h, label, color, sub="", icon="lambda", nid=None):
    nodes.append(dict(x=x, y=y, w=w, h=h, label=label, color=color, sub=sub,
                      icon=icon, id=nid or ("n%d" % len(nodes))))


def E(a, b, label="", color="#7FB3FF", dash=False, t=0.5):
    edges.append(dict(a=a, b=b, label=label, color=color, dash=dash, t=t))


def NOTE(x, y, w, h, text):
    notes.append(dict(x=x, y=y, w=w, h=h, text=text))


def T(x, y, s, size=15, color=TXT, bold=False):
    texts.append(dict(x=x, y=y, s=s, size=size, color=color, bold=bold))


# ═══════════════ タイトル ═══════════════
T(40, 52, "BrightStar 統合LINEアシスタント  インフラアーキテクチャ", 30, TXT, True)
T(40, 84, "正 = BS_INF-01〜10 設計書 ／ ap-northeast-1 ・ アカウント 603319838936 ・ stage=dev", 16, MUTE)
T(40, 108, "実機照合日 2026-09-19（Lambda 11本・DynamoDB 15表・S3 1本／デプロイ済みコードとリポジトリの md5 一致を確認済み）",
  15, "#7ED9A0")

# ═══════════════ 利用者 → LINE（チャネルごとに縦一直線） ═══════════════
COL = [84, 334, 584, 834, 1084, 1334]
WD = [228, 228, 228, 228, 228, 240]

G(64, 140, 1530, 92, "利用者（社内 約100名）／ 役割は社員名簿(roster)の role で判定", MUTE, True)
USERS = [("一般社員", "employee"), ("総務", "hr / soumu"), ("人事", "hr"),
         ("営業", "sales"), ("社内ツール", "機械クライアント"), ("講師", "teacher")]
for i, (nm, role) in enumerate(USERS):
    N(COL[i], 168, WD[i], 52, nm, "#39506E", role, "user", nid="u%d" % i)

G(64, 262, 1530, 122, "LINE Platform（同一 Provider ＝ userId が全チャネルで一致）", LINE_GR, False)
ACCS = [("BS社員管理", "shain"), ("BS総務", "soumu"), ("BS人事", "jinji"),
        ("BS営業", "eigyo"), (None, None), ("BS研修", "kenshu")]
for i, (nm, key) in enumerate(ACCS):
    if nm:
        N(COL[i], 296, WD[i], 64, nm, LINE_GR, "公式アカウント", "user", nid="acc_" + key)

# ═══════════════ AWS Cloud ═══════════════
G(40, 408, 2020, 800, "AWS クラウド  ap-northeast-1", "#4E7FB8", False)

G(64, 448, 1530, 132,
  "公開エンドポイント層（AWS認証なし＝authType NONE／防御はアプリ層の LINE署名検証）", C_NET)
EPS = [("Function URL" + NL + "社員", "NONE", "fu_shain"),
       ("Function URL" + NL + "総務", "NONE", "fu_soumu"),
       ("Function URL" + NL + "人事", "NONE", "fu_jinji"),
       ("Function URL" + NL + "営業", "NONE", "fu_eigyo"),
       ("Function URL" + NL + "営業API", "AWS_IAM", "fu_eigyo_api"),
       ("API Gateway" + NL + "HTTP API（研修）", "7ルート", "apigw_kenshu")]
for i, (nm, sub, nid) in enumerate(EPS):
    N(COL[i], 482, WD[i], 78, nm, C_NET, sub, "apigw", nid=nid)

G(64, 600, 1530, 210,
  "Lambda 層  Python 3.12 / arm64（営業のみ x86_64）／ 外部ライブラリなし（標準ライブラリ + boto3）", C_COMP)
L1 = [("shain-webhook", "512MB / 29s", "l_shain"),
      ("soumu-webhook", "512MB / 29s", "l_soumu"),
      ("hr-webhook", "256MB / 29s", "l_hr"),
      ("eigyo LineWebhookFn", "256MB / 300s", "l_eigyo"),
      ("eigyo ApiFn", "256MB / 300s", "l_eigyoapi"),
      ("kenshu Line / WeChat" + NL + "Web（3関数）", "1024MB / 20s", "l_kenshu")]
for i, (nm, sub, nid) in enumerate(L1):
    N(COL[i], 634, WD[i], 70, nm, C_COMP, sub, "lambda", nid=nid)
N(COL[1], 722, 228, 70, "soumu-reminder", C_COMP, "催促 / 予約 / 一斉送信", "lambda", nid="l_rem")
N(COL[2], 722, 228, 70, "hr-reconcile", C_COMP, "日次点検 120s", "lambda", nid="l_rec")
N(COL[5], 722, 240, 70, "kenshu Reminder", C_COMP, "開講1h前", "lambda", nid="l_krem")

# --- 共有データ ---
G(64, 830, 860, 348,
  "共有データ  BrightstarHr-dev が所有 ／ 他4スタックは「テーブル名」で参照", C_DB)
for i, (nm, sub, nid) in enumerate([("roster", "社員名簿・PITR有", "t_roster"),
                                    ("auth", "日次認証・TTL", "t_auth"),
                                    ("employees", "紐付け・PITR有", "t_emp"),
                                    ("submissions", "提出記録・GSI1", "t_sub")]):
    N(84 + i * 208, 872, 196, 72, nm, C_DB, sub, "ddb", nid=nid)
N(84, 962, 400, 158, "S3", C_STOR,
  "brightstar-hr-dev-603319838936" + NL +
  "hr/{年}/{月}/{worktimes|expenses|others}" + NL +
  "hr/template ・ pending ・ exports" + NL +
  "公開遮断 / SSE-S3 / バージョニング有" + NL +
  "60日→Deep Archive ／ 365日削除", "s3", nid="s3")
NOTE(500, 962, 404, 158,
     "個人情報の所在" + NL +
     "氏名・社員番号・所属・勤務実績・通勤経路" + NL +
     "マイナンバー / 口座 / 在留カードは扱わない" + NL + NL +
     "横断閲覧（一覧・CSV・一括DL・削除）は" + NL +
     "役割 hr / soumu に限定（BS_INF-07 §3）")

# --- チャネル固有データ ---
G(946, 830, 648, 348, "チャネル固有データ", C_DB)
for i, (nm, sub, nid) in enumerate([("session", "社員・当日モード", "t_sess"),
                                    ("bookings", "催促予約", "t_book"),
                                    ("broadcasts", "配信・既読確認", "t_bc")]):
    N(966 + i * 210, 872, 198, 72, nm, C_DB, sub, "ddb", nid=nid)
N(966, 962, 408, 72, "kenshu 6表", C_DB,
  "courses / enrollments / groups / students / results", "ddb", nid="t_kenshu")
N(1386, 962, 188, 72, "EkiCommute 2表", C_DB, "Staff / Cache(TTL)", "ddb", nid="t_eki")
T(966, 1070, "全15表 ＝ PAY_PER_REQUEST（月末に負荷が偏るためオンデマンド）", 14, MUTE)
T(966, 1094, "暗号化：AWS管理キー（営業2表のみ AWS所有キー）／ CMK は不採用", 14, MUTE)
T(966, 1118, "PITR は roster / employees / submissions のみ有効", 14, MUTE)

# --- 右カラム ---
G(1616, 448, 424, 362, "定期実行  Amazon EventBridge", C_APP)
for i, (nm, sub, nid) in enumerate([
        ("soumu reminder-schedule", "cron(0 0 25,28 * ? *) ＝ 毎月25/28日 9:00 JST", "e_rem"),
        ("soumu booking-poller", "rate(10 minutes) 予約催促の実行", "e_poll"),
        ("hr reconcile-schedule", "cron(0 15 * * ? *) ＝ 毎日 0:00 JST", "e_rec"),
        ("kenshu ReminderTick", "rate(10 minutes) 開講1h前", "e_krem")]):
    N(1636, 486 + i * 78, 386, 66, nm, C_APP, sub, "evb", nid=nid)

G(1616, 830, 424, 160, "機密情報・暗号化", C_SEC)
N(1636, 868, 386, 46, "SSM Parameter Store（SecureString）", C_SEC, "", "ssm", nid="ssm")
T(1636, 938, "/{app}/dev/line/{secret,token} ×5チャネル", 13, MUTE)
T(1636, 962, "値はソース・CDK・設計書に記載しない／CMK 不採用", 13, MUTE)

G(1616, 1010, 424, 168, "監視・ログ", C_APP)
N(1636, 1048, 386, 46, "CloudWatch Logs", C_APP, "", "cw", nid="cw")
T(1636, 1118, "保持 30日（営業のみ14日）・無期限保持は 0 件", 13, "#7ED9A0")
T(1636, 1144, "CloudWatch アラーム 0 件 ← 要対応", 13, "#FF9B9B")

# --- 外部サービス ---
G(2080, 408, 520, 800, "外部サービス（AWS外）", "#C8A45B", False)
for i, (nm, sub, nid, col, ic) in enumerate([
        ("企業微信 / WeChat", "研修のみ・API GW /wechat", "x_wecom", "#C8A45B", "user"),
        ("Zoom API", "研修の開講リンク自動発行", "x_zoom", "#C8A45B", "user"),
        ("駅探（ekitan）", "営業・通勤経路の取得", "x_eki", "#C8A45B", "user"),
        ("Amazon Location geo-places", "住所→最寄駅（リソース不要）", "x_loc", C_NET, "loc"),
        ("Amazon Bedrock", "権限のみ付与・現在 未使用", "x_br", C_ML, "bedrock"),
        ("sons02 メール校正ツール", "人事からリンク誘導のみ", "x_mail", "#C8A45B", "user")]):
    N(2104, 456 + i * 82, 472, 64, nm, col, sub, ic, nid=nid)
NOTE(2104, 960, 472, 218,
     "外部依存の扱い" + NL +
     "・駅探＝HTML構造の変更で壊れる想定。" + NL +
     "  データ源の差し替えを契約事項としている" + NL +
     "・Bedrock は東京で inference-profile が必須" + NL +
     "・企業微信の認証情報は現在 Lambda 環境変数に" + NL +
     "  平文。SSM SecureString 化が必要（BS_INF-08 §4）" + NL +
     "・外部到達は全て HTTPS。VPC / NAT GW は持たない")

# ═══════════════ フロー ═══════════════
BLUE, GREEN, ORANGE, PINK, GRAY = "#7FB3FF", "#5FD68A", "#FFB454", "#FF7AC8", "#7A8CA6"
for i, key in enumerate(["shain", "soumu", "jinji", "eigyo", None, "kenshu"]):
    if not key:
        continue
    E("u%d" % i, "acc_" + key, "", GRAY)
    E("acc_" + key, "fu_" + key if key != "kenshu" else "apigw_kenshu", "", GRAY)
E("u4", "fu_eigyo_api", "SigV4", GRAY)
for a, b in [("fu_shain", "l_shain"), ("fu_soumu", "l_soumu"), ("fu_jinji", "l_hr"),
             ("fu_eigyo", "l_eigyo"), ("fu_eigyo_api", "l_eigyoapi"),
             ("apigw_kenshu", "l_kenshu")]:
    E(a, b, "", GRAY)
# 線上は番号のみ。内容は「処理フロー」注記に書く（線が短くても読めるようにするため）
E("l_shain", "t_roster", "①", BLUE, t=0.55)
E("l_shain", "s3", "②", GREEN, t=0.60)
E("l_soumu", "t_sub", "③", BLUE, t=0.55)
E("l_soumu", "s3", "⑥", GREEN, t=0.68)
E("e_rem", "l_rem", "④", ORANGE, t=0.42)
E("e_poll", "l_rem", "⑦", ORANGE, t=0.36)
E("e_rec", "l_rec", "⑧", ORANGE, t=0.36)
E("e_krem", "l_krem", "", ORANGE)
E("l_rem", "t_bc", "⑤", PINK, t=0.52)
E("l_rem", "acc_shain", "④", PINK, t=0.90)
E("l_soumu", "l_rem", "非同期", GRAY, dash=True, t=0.5)
E("l_rec", "t_roster", "⑧", ORANGE, t=0.62)
E("l_eigyo", "x_loc", "", GRAY)
E("l_eigyo", "x_eki", "", GRAY)
E("l_kenshu", "x_zoom", "", GRAY)
E("l_kenshu", "x_wecom", "", GRAY)
E("l_hr", "x_mail", "", GRAY, dash=True)
E("l_shain", "ssm", "", GRAY, dash=True)
E("t_sess", "l_shain", "", GRAY)
E("t_book", "l_rem", "", GRAY)
E("l_kenshu", "t_kenshu", "", GRAY)
E("l_eigyo", "t_eki", "", GRAY)

# ═══════════════ 凡例・注記（AWS クラウド枠の下） ═══════════════
G(40, 1240, 440, 190, "凡例（線の色）", MUTE)
LEG = [(BLUE, "① ③ 同期リクエスト"),
       (GREEN, "② ⑥ S3 経路"),
       (ORANGE, "④ ⑦ ⑧ 定期実行"),
       (PINK, "⑤ push 配信"),
       (GRAY, "経路（実線）／非同期・参照（破線）")]
for i, (c, s) in enumerate(LEG):
    T(66, 1288 + i * 26, "━━", 16, c, True)
    T(116, 1288 + i * 26, s, 14, TXT)

NOTE(500, 1240, 700, 190,
     "処理フロー（線上の番号に対応）" + NL +
     "① 本人確認：「所属部署 お名前」→ roster 照合 → auth に当日認証（TTL＝当日限り）" + NL +
     "② 提出：Excel / PDF / 画像 → S3 へ保存し submissions に記録" + NL +
     "③ 回収：総務が submissions の GSI1（月×種別）を Query して提出状況を横断集計" + NL +
     "④ 催促：EventBridge（25/28日）→ reminder →「社員チャネルの token」で push" + NL +
     "⑤ 既読確認：社員が postback → broadcasts に確認記録（配信IDは画面に出さない）" + NL +
     "⑥ ダウンロード：ZIP/CSV を exports/ に生成 → 署名付き短縮URL → presigned で S3 から取得" + NL +
     "⑦ 予約催促：bookings を 10分間隔のポーラーが拾い、実行時点の未提出者のみへ送信" + NL +
     "⑧ 日次点検：毎日0:00 に到達性を確認し、到達不可なら紐付けを解除")

NOTE(1220, 1240, 560, 190,
     "なぜ authType = NONE か" + NL +
     "LINE Platform は Webhook に SigV4 を付けられない。" + NL +
     "AWS_IAM にすると LINE からの全リクエストが 403 になる。" + NL +
     "よって AWS 層は通し、アプリ層で HMAC-SHA256 の" + NL +
     "署名検証を必ず行う（BS_INF-05 §3）。" + NL +
     "→ チャネルシークレットの漏洩が単一障害点。" + NL +
     "  SSM SecureString にのみ保持する。")

NOTE(1810, 1240, 540, 190,
     "所有区分（スタック依存）" + NL +
     "・BrightstarHr-dev（人事）が roster / auth /" + NL +
     "  employees / submissions / S3 を所有。" + NL +
     "  他4スタックは名前で参照するだけ。" + NL +
     "・初回デプロイは人事を最初に行う。" + NL +
     "・人事スタックの削除は他4チャネルを巻き込む。" + NL +
     "・各チャネルは独立スタック＝障害・改修が波及しない。")

NOTE(40, 1460, 440, 290,
     "設計の要点：VPC を使わない" + NL +
     "接続先は全てマネージド" + NL +
     "サービスと外部 HTTPS。" + NL +
     "VPC に入れると ENI 生成で" + NL +
     "コールドスタートが延び、" + NL +
     "NAT GW の固定費も発生する。" + NL + NL +
     "入口側の固定費はゼロ" + NL +
     "（CloudFront / WAF / ALB /" + NL +
     "独自ドメインを使わない）。" + NL + NL +
     "外部ライブラリも持たない" + NL +
     "（標準ライブラリ + boto3）。")

NOTE(500, 1460, 1030, 290,
     "早期対応を推奨する課題（BS_INF シリーズ 横断サマリ）" + NL +
     "① CloudWatch アラームが 0 件。月2回しか動かない催促が失敗しても誰も気付かない　【高】BS_INF-09 §4" + NL +
     "② 企業微信の認証情報が Lambda 環境変数に平文。SSM SecureString 化が必要　　　　　　【高】BS_INF-08 §4" + NL +
     "③ 全 DynamoDB / S3 が RemovalPolicy.DESTROY。人事スタック削除で全社データ消失　　　【高】BS_INF-03 §7" + NL +
     "④ /dl の署名に有効期限が無い（実体は exports/ が3日で消えるため露出は最大3日）　　　【中】BS_INF-04 §6" + NL +
     "⑤ 60日超の月は Deep Archive へ移行済みで一括DLが失敗する（復元処理は未実装）　　　　【中】BS_INF-04 §5" + NL +
     "⑥ IAM が粗い（読み取りのみの関数にも DeleteItem）。ログに氏名・社員番号が出力される　【中】BS_INF-07 / 09" + NL + NL +
     "※ 脆弱性検査（SAST / DAST）・ペネトレーションテストは未実施。実施済みは機能面の単体・結合テストのみ。")

NOTE(1560, 1460, 1040, 290,
     "2026-09-19 に実施した是正と検証" + NL +
     "・CloudWatch Logs の無期限保持を解消。研修 / 人事 / 総務 / 社員の全 Lambda に logRetention=30日 を追加し4スタックを再デプロイ。" + NL +
     "  brightstar / Eki 系 19 ロググループのうち無期限保持は 0 件になった（営業のみ従来どおり14日）。" + NL +
     "・旧スタック由来の孤立ロググループ 5 件（計 791KB・最終ログ 2026-06）を、対応する Lambda の不在を確認のうえ削除。" + NL +
     "・デプロイ済み Lambda とリポジトリの同期を機械検証。全11関数・122ファイルの md5 が一致（差分なし）。" + NL +
     "  検証方法＝各関数の Code.Location から配布 zip を取得・展開し、リポジトリ側とファイル単位で md5 比較。" + NL +
     "  営業のみ build/ が .gitignore のため、build.py のコピー規則で git 管理下の元ファイルへ読み替えて突合。" + NL + NL +
     "この図と BS_INF-01〜10 の記載値は、すべて上記時点の実機照会結果であり、設計当初の想定値ではない。")


# ═══════════════ 出力：SVG ═══════════════
def node_by_id(i):
    for n in nodes:
        if n["id"] == i:
            return n
    raise KeyError(i)


def anchor(a, b):
    ax, ay = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    bx, by = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    dx, dy = bx - ax, by - ay

    def pt(n, cx, cy, dx, dy):
        if dx == 0 and dy == 0:
            return cx, cy
        hw, hh = n["w"] / 2 + 4, n["h"] / 2 + 4
        s = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
        return cx + dx * s, cy + dy * s
    return pt(a, ax, ay, dx, dy), pt(b, bx, by, -dx, -dy)


def esc(s):
    return html.escape(s, quote=True)


svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
       'font-family="Meiryo, Yu Gothic, Noto Sans JP, sans-serif">' % (W, H, W, H),
       '<rect width="%d" height="%d" fill="%s"/>' % (W, H, BG), '<defs>']
for c in [BLUE, GREEN, ORANGE, PINK, GRAY]:
    svg.append('<marker id="ar%s" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto">'
               '<path d="M0,0 L0,6 L9,3 z" fill="%s"/></marker>' % (c.lstrip("#"), c))
svg.append('</defs>')

for g in groups:
    svg.append('<rect x="%d" y="%d" width="%d" height="%d" rx="12" fill="%s" stroke="%s" '
               'stroke-width="2" %s/>'
               % (g["x"], g["y"], g["w"], g["h"], PANEL, g["color"],
                  'stroke-dasharray="7 5"' if g["dash"] else ""))
    svg.append('<text x="%d" y="%d" font-size="15" font-weight="bold" fill="%s">%s</text>'
               % (g["x"] + 14, g["y"] + 25, g["color"], esc(g["label"])))

for e in edges:
    a, b = node_by_id(e["a"]), node_by_id(e["b"])
    (x1, y1), (x2, y2) = anchor(a, b)
    svg.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2.4" '
               '%s marker-end="url(#ar%s)" opacity="0.9"/>'
               % (x1, y1, x2, y2, e["color"],
                  'stroke-dasharray="8 5"' if e["dash"] else "", e["color"].lstrip("#")))
    if e["label"]:
        tt = e["t"]
        mx_, my_ = x1 + (x2 - x1) * tt, y1 + (y2 - y1) * tt
        tw = len(e["label"]) * 9.2 + 16
        svg.append('<rect x="%.1f" y="%.1f" width="%.1f" height="24" rx="7" fill="%s" '
                   'stroke="%s" stroke-width="1.2"/>' % (mx_ - tw / 2, my_ - 12, tw, BG, e["color"]))
        svg.append('<text x="%.1f" y="%.1f" font-size="13.5" fill="%s" text-anchor="middle">%s</text>'
                   % (mx_, my_ + 5, e["color"], esc(e["label"])))

for n in nodes:
    svg.append('<rect x="%d" y="%d" width="%d" height="%d" rx="10" fill="%s" stroke="%s" '
               'stroke-width="2.2"/>' % (n["x"], n["y"], n["w"], n["h"], PANEL, n["color"]))
    svg.append('<rect x="%d" y="%d" width="10" height="%d" rx="5" fill="%s"/>'
               % (n["x"], n["y"], n["h"], n["color"]))
    ls = n["label"].split(NL)
    ty = n["y"] + (24 if (len(ls) > 1 or n["sub"]) else n["h"] / 2 + 6)
    for i, l in enumerate(ls):
        svg.append('<text x="%d" y="%d" font-size="15" font-weight="bold" fill="%s">%s</text>'
                   % (n["x"] + 22, ty + i * 19, TXT, esc(l)))
    if n["sub"]:
        sy = ty + len(ls) * 19 + 2
        for j, sl in enumerate(n["sub"].split(NL)):
            svg.append('<text x="%d" y="%d" font-size="12.5" fill="%s">%s</text>'
                       % (n["x"] + 22, sy + j * 17, MUTE, esc(sl)))

for nt in notes:
    svg.append('<path d="M%d,%d h%d v%d l-18,18 h-%d z" fill="%s" stroke="#D9C97A" stroke-width="1.5"/>'
               % (nt["x"], nt["y"], nt["w"], nt["h"] - 18, nt["w"] - 18, NOTE_BG))
    for i, l in enumerate(nt["text"].split(NL)):
        svg.append('<text x="%d" y="%d" font-size="13" fill="%s" %s>%s</text>'
                   % (nt["x"] + 14, nt["y"] + 25 + i * 19, NOTE_FG,
                      'font-weight="bold"' if i == 0 else "", esc(l)))

for t in texts:
    svg.append('<text x="%d" y="%d" font-size="%s" fill="%s" %s>%s</text>'
               % (t["x"], t["y"], t["size"], t["color"],
                  'font-weight="bold"' if t["bold"] else "", esc(t["s"])))
svg.append("</svg>")

os.makedirs(OUT, exist_ok=True)
svg_path = os.path.join(OUT, "BS_インフラアーキテクチャ図.svg")
with open(svg_path, "w", encoding="utf-8") as f:
    f.write(NL.join(svg))
print("SVG   :", svg_path)


# ═══════════════ 出力：drawio ═══════════════
def dstyle(color, icon):
    return ("sketch=0;outlineConnect=0;fontColor=#FFFFFF;gradientColor=none;fillColor=%s;"
            "strokeColor=none;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;"
            "align=center;html=1;fontSize=11;aspect=fixed;shape=mxgraph.aws4.resourceIcon;"
            "resIcon=%s;" % (color, RES.get(icon, RES["lambda"])))


BR = "&lt;br&gt;"
mx = ['<mxfile host="app.diagrams.net"><diagram name="BrightStar インフラ">',
      '<mxGraphModel dx="1600" dy="900" grid="0" page="1" pageWidth="%d" pageHeight="%d" '
      'background="%s" math="0" shadow="0"><root>'
      '<mxCell id="0"/><mxCell id="1" parent="0"/>' % (W, H, BG)]
cid = 100
for g in groups:
    mx.append('<mxCell id="g%d" value="%s" style="rounded=1;fillColor=%s;strokeColor=%s;dashed=%d;'
              'verticalAlign=top;align=left;spacingLeft=10;fontColor=%s;fontSize=14;fontStyle=1;'
              'html=1;" vertex="1" parent="1">'
              '<mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/></mxCell>'
              % (cid, esc(g["label"]), PANEL, g["color"], 1 if g["dash"] else 0, g["color"],
                 g["x"], g["y"], g["w"], g["h"]))
    cid += 1
for n in nodes:
    lbl = esc(n["label"]).replace("\n", BR)
    if n["sub"]:
        lbl += BR + '&lt;font color="#9FB4CE"&gt;' + esc(n["sub"]).replace("\n", BR) + '&lt;/font&gt;'
    mx.append('<mxCell id="%s" value="%s" style="rounded=1;fillColor=%s;strokeColor=%s;'
              'fontColor=#FFFFFF;fontSize=12;fontStyle=1;html=1;align=left;spacingLeft=52;'
              'verticalAlign=middle;" vertex="1" parent="1">'
              '<mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/></mxCell>'
              % (n["id"], lbl, PANEL, n["color"], n["x"], n["y"], n["w"], n["h"]))
    mx.append('<mxCell id="%s_i" value="" style="%s" vertex="1" parent="1">'
              '<mxGeometry x="%d" y="%d" width="34" height="34" as="geometry"/></mxCell>'
              % (n["id"], dstyle(n["color"], n["icon"]), n["x"] + 10, n["y"] + n["h"] / 2 - 17))
for e in edges:
    mx.append('<mxCell id="e%d" value="%s" style="edgeStyle=orthogonalEdgeStyle;rounded=1;'
              'strokeColor=%s;strokeWidth=2;dashed=%d;fontColor=%s;fontSize=11;html=1;" '
              'edge="1" parent="1" source="%s" target="%s">'
              '<mxGeometry relative="1" as="geometry"/></mxCell>'
              % (cid, esc(e["label"]), e["color"], 1 if e["dash"] else 0,
                 e["color"], e["a"], e["b"]))
    cid += 1
for nt in notes:
    mx.append('<mxCell id="nt%d" value="%s" style="shape=note;whiteSpace=wrap;html=1;size=18;'
              'fillColor=%s;strokeColor=#D9C97A;fontColor=%s;fontSize=11;align=left;'
              'verticalAlign=top;spacingLeft=6;spacingTop=4;" vertex="1" parent="1">'
              '<mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/></mxCell>'
              % (cid, esc(nt["text"]).replace("\n", BR), NOTE_BG, NOTE_FG,
                 nt["x"], nt["y"], nt["w"], nt["h"]))
    cid += 1
for t in texts:
    mx.append('<mxCell id="t%d" value="%s" style="text;html=1;fontColor=%s;fontSize=%s;fontStyle=%d;'
              'align=left;verticalAlign=middle;" vertex="1" parent="1">'
              '<mxGeometry x="%d" y="%d" width="760" height="20" as="geometry"/></mxCell>'
              % (cid, esc(t["s"]), t["color"], t["size"], 1 if t["bold"] else 0, t["x"], t["y"] - 14))
    cid += 1
mx.append("</root></mxGraphModel></diagram></mxfile>")
dio_path = os.path.join(OUT, "BS_インフラアーキテクチャ図.drawio")
with open(dio_path, "w", encoding="utf-8") as f:
    f.write(NL.join(mx))
print("drawio:", dio_path)

hp = os.path.join(OUT, "_render.html")
with open(hp, "w", encoding="utf-8") as f:
    f.write('<html><head><meta charset="utf-8"><style>html,body{margin:0;background:%s}</style>'
            '</head><body>%s</body></html>' % (BG, open(svg_path, encoding="utf-8").read()))
print("html  :", hp)
