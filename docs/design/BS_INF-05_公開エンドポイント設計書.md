# BrightStar 統合LINEアシスタント 公開エンドポイント設計書（Function URL / API Gateway）

| 項目 | 内容 |
|---|---|
| 文書番号 | BS_INF-05 |
| 文書名 | 公開エンドポイント設計書 |
| 対象システム | BrightStar 統合LINEアシスタント（5チャネル） |
| 版数 | 初版 |
| 最終更新 | 2026-09-19 |
| 実機照合 | 2026-09-19 |

---

## 1. 方針

| 項目 | 設計 | 理由 |
|---|---|---|
| 公開方式 | **原則 Lambda Function URL**。研修チャネルのみ API Gateway (HTTP API) | Function URL は追加費用ゼロ・構成要素ゼロで HTTPS 入口が手に入る。API GW が必要になるのは**1 つのドメインで複数の入口を出し分けたい場合**のみ |
| 認証方式 | **`authType=NONE`（AWS 認証なし）** + **アプリ層で LINE 署名検証** | LINE Platform は Webhook を送る際に **SigV4 を付けられない**。AWS_IAM を設定すると LINE からのリクエストが全て 403 になる。したがって AWS 層では通し、**アプリ層で HMAC-SHA256 署名を必ず検証**する |
| CloudFront / WAF | **使わない** | 入口は LINE Platform からの Webhook のみで、一般利用者がブラウザで叩く画面が無い。WAF の主目的（Web アプリへの攻撃遮断）に該当しない。また **AWS WAF は HTTP API (v2) にアタッチできない** |
| カスタムドメイン | **設定しない** | URL を目にするのは LINE Developers の設定画面のみ。独自ドメイン・ACM 証明書の運用コストに見合わない |
| CORS | **設定しない** | ブラウザからのクロスオリジン呼び出しが発生しない。「不要なのに開ける」事故を避ける |

---

## 2. エンドポイント一覧（2026-09-19 実測）

### 2.1 Lambda Function URL

| チャネル | URL | 認証 | 呼び出しモード |
|---|---|---|---|
| 人事 | `https://z5kzr2rgvkd4vxgkyepd3z5crq0spbsw.lambda-url.ap-northeast-1.on.aws/` | NONE | BUFFERED |
| 総務 | `https://zguhjjngs3hn6uwrs37mbsvtju0nkqcj.lambda-url.ap-northeast-1.on.aws/` | NONE | BUFFERED |
| 社員 | `https://juxjey4vihgbddhuurpbctb6ha0uunjl.lambda-url.ap-northeast-1.on.aws/` | NONE | BUFFERED |
| 営業（LINE） | `https://iz5o26r4nifhjbtosr2n5b37gy0nvcch.lambda-url.ap-northeast-1.on.aws/` | NONE | BUFFERED |
| 営業（API） | `https://cu2okzssncdc46kzqedf6xn3de0gdcfy.lambda-url.ap-northeast-1.on.aws/` | **AWS_IAM** | BUFFERED |

CORS はいずれも未設定。

> 営業の API 用 Function URL だけ **AWS_IAM** なのは、LINE ではなく**機械クライアント（社内ツール）から呼ぶ想定**であり、SigV4 を付けられるため。**付けられる経路には必ず AWS 認証を掛ける**、という原則を適用している。

### 2.2 API Gateway（研修チャネルのみ）

API ID `2xqgja49x8` / HTTP API / ステージ `$default`（autoDeploy 有効）

| ルート | 認証 | 統合先 | 用途 |
|---|---|---|---|
| `POST /line` | NONE | LineWebhookFunction | LINE Webhook |
| `GET /wechat` | NONE | WebhookFunction | 企業微信 URL 検証（echostr 応答） |
| `POST /wechat` | NONE | WebhookFunction | 企業微信 メッセージ受信 |
| `POST /web/login` | NONE | WebFunction | 受講者向け Web ログイン |
| `POST /web/submit` | NONE | WebFunction | 受講者の回答送信 |
| `POST /web/results` | NONE | WebFunction | 結果取得 |
| `POST /web/my-results` | NONE | WebFunction | 自分の結果取得 |

**研修だけ API Gateway を使う理由**：LINE・企業微信・Web 画面という**3 系統の入口を 1 つのドメインにまとめる**必要があった。Function URL は 1 関数 1 URL のため、入口ごとにドメインが変わってしまい、企業微信側のコールバック設定や Web 画面の呼び先が煩雑になる。

---

## 3. 認証・アクセス制御の層構造

エンドポイント自体は公開だが、**3 層で守る**。

| 層 | 方式 | 内容 |
|---|---|---|
| ① 送信元検証 | **LINE 署名検証（HMAC-SHA256）** | リクエストヘッダ `X-Line-Signature` と、チャネルシークレットで計算した署名を `hmac.compare_digest` で比較。不一致は即 401。**LINE 以外からのリクエストはここで全て落ちる** |
| ② 本人確認 | **社員名簿（roster）照合** | 「所属部署 お名前」で名簿と突合し、在籍者本人としてのみ登録を許す。**1 社員番号＝1 LINE アカウントの占用ロック**で成りすまし・二重登録を防ぐ。認証は TTL により**当日限り** |
| ③ 認可 | **役割（role / roles）によるチャネルゲート** | 総務チャネルは `hr` または `soumu`、研修（講師）は `teacher`、営業は `sales` を要求。役割を持たない社員は操作できず、他チャネルへ誘導される。一斉送信はさらに `admin` を要求 |

> **署名検証が単一の防波堤**である点は認識しておく必要がある。チャネルシークレットが漏れれば、任意の偽装リクエストを送れる。シークレットは SSM SecureString にのみ保持し、ソース・環境変数に置かない（[BS_INF-08](BS_INF-08_SSM・暗号化設計書.md)）。

---

## 4. 副次エンドポイント（同一 Function URL 上の GET 経路）

webhook 用の Function URL は、POST（LINE Webhook）以外に以下の GET/POST 経路も処理する。

| 経路 | チャネル | 認証 | 用途 |
|---|---|---|---|
| `GET /dl?key=…&sig=…` | 人事・総務・社員 | **HMAC 署名**（AWS 認証なし） | ZIP・CSV・提出物のダウンロード。S3 presigned URL へ 302（[BS_INF-04](BS_INF-04_S3設計書.md) §4） |
| `GET /bcast?uid=…&exp=…&sig=…` | 総務 | **HMAC 署名（有効期限30分つき）** | 一斉送信の入力フォーム（HTML）。PC から長文を書くため |
| `POST /bcast` | 総務 | 同上 | フォーム送信 |

> `/bcast` は署名に**失効時刻を含めている**が、`/dl` は含めていない。同じ仕組みなので `/dl` も揃えるべき（[BS_INF-04](BS_INF-04_S3設計書.md) §6 課題1）。

---

## 5. 制約と回避設計

| 制約 | 値 | 回避設計 |
|---|---|---|
| **LINE の応答タイムアウト** | 30 秒 | 全 webhook のタイムアウトを **29 秒**に設定。重い処理（催促送信・一斉送信・経路取得）は ack を即返し、**別 Lambda を非同期 Invoke** して結果を push で届ける |
| LINE ボタンの URL 長 | 1000 文字 | 署名付き短縮 URL（`/dl`）を挟んで presigned URL へリダイレクト |
| LINE reply token | 1 回・数十秒のみ有効 | 複数回送る場合は reply ではなく push を使う。QuickReply 付きの push は `push_message` を使用 |
| Function URL のレート制限 | アカウント同時実行数に依存 | 社内 100 名規模では問題にならない。**予約同時実行数は設定していない** |

---

## 6. 既知の課題

| # | 深刻度 | 区分 | 内容 | 推奨対応 | 要否 |
|---|---|---|---|---|---|
| 1 | 中 | 構成 | Function URL・API Gateway とも**レート制限が無い**。署名検証前の大量リクエストで Lambda が無制限にスケールし、費用が跳ねる（署名不一致でも Lambda は起動している） | API GW 側はステージのスロットリング設定、Function URL 側は予約同時実行数の設定で上限を作る | 将来対応 |
| 2 | 低 | 監査 | API Gateway の詳細メトリクス（`DetailedMetricsEnabled`）が無効。ルート別の呼び出し状況が見えない | 有効化（追加費用は軽微） | 将来対応 |
| 3 | 低 | 構成 | Function URL は**削除・再作成で URL が変わる**。変わると LINE Developers 側の Webhook URL 設定を手で直す必要がある | 運用手順として明記済み（[BS_INF-10](BS_INF-10_CDK構成管理・デプロイ設計書.md) §6） | 対応不要（周知のみ） |

---

## 7. 費用

Function URL は**追加費用なし**（Lambda の課金のみ）。API Gateway (HTTP API) はリクエスト課金（100万件あたり約 $1.0）で、社内利用の規模では無視できる。CloudFront・WAF・ALB・独自ドメインを使わないため、**入口側の固定費はゼロ**。

---

## 8. 関連文書

- Lambda 本体 … [BS_INF-02](BS_INF-02_Lambda設計書.md)
- ダウンロード経路 … [BS_INF-04 S3設計書](BS_INF-04_S3設計書.md)
- 認証情報の保管 … [BS_INF-08](BS_INF-08_SSM・暗号化設計書.md)
