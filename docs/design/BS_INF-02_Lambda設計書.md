# BrightStar 統合LINEアシスタント Lambda設計書

| 項目 | 内容 |
|---|---|
| 文書番号 | BS_INF-02 |
| 文書名 | Lambda設計書 |
| 対象システム | BrightStar 統合LINEアシスタント（5チャネル） |
| 版数 | 初版 |
| 最終更新 | 2026-09-19 |
| 実機照合 | 2026-09-19（全11関数） |

---

## 1. 方針

| 項目 | 設計 | 理由 |
|---|---|---|
| 実行基盤 | **Lambda のみ**（コンテナ・EC2 を使わない） | LINE Bot は「メッセージが来たときだけ動く」典型的なイベント駆動。常時起動リソースはコストの無駄で、パッチ運用も発生する |
| ランタイム | **Python 3.12** 全関数統一 | チャネル間で共通モジュール（認証・多言語・S3操作）をコピー共有しているため、ランタイムを揃えないと共有できない |
| アーキテクチャ | **arm64（Graviton2）**（営業のみ x86_64） | 同性能で **約20%安い**。営業だけ x86_64 なのは requests/BeautifulSoup を含む Layer を x86 でビルド済みのため（BS_INF-10 §5） |
| 外部ライブラリ | **使わない**（標準ライブラリ + boto3 のみ）。営業のみ例外 | ①Layer ビルド・Docker が不要でデプロイが単純 ②**依存パッケージ経由の脆弱性リスクが構造的に発生しない** ③コールドスタートが速い |
| VPC | **配置しない** | 接続先は全て AWS のマネージドサービス（DynamoDB/S3/SSM）と外部 HTTPS。VPC に入れると ENI 生成でコールドスタートが延び、NAT GW の固定費も発生する |
| 同時実行数の予約 | **設定しない** | 社内 100 名規模・月次ピークでもアカウント既定（1000）に遠く及ばない |

### xlsx を標準ライブラリだけで読む

提出された Excel の年月・氏名を読むために openpyxl を入れるとサイズも依存も増える。
本システムは **xlsx = zip + XML** であることを利用し、`zipfile` + `xml.etree.ElementTree` で必要セルだけを読む自作モジュール（`common/xlsx.py`）を使う。
これにより「外部ライブラリゼロ」の方針を崩さずに Excel を扱える。

---

## 2. 関数一覧（2026-09-19 実測）

| # | 関数名 | チャネル | アーキ | メモリ | タイムアウト | 起動元 |
|---|---|---|---|---|---|---|
| 1 | `brightstar-kenshu-dev-LineWebhookFunction…` | 研修 | arm64 | 1024MB | 20s | API GW `POST /line` |
| 2 | `brightstar-kenshu-dev-WebhookFunction…` | 研修 | arm64 | 1024MB | 20s | API GW `GET/POST /wechat` |
| 3 | `brightstar-kenshu-dev-WebFunction…` | 研修 | arm64 | 1024MB | 20s | API GW `POST /web/*` |
| 4 | `brightstar-kenshu-dev-ReminderFunction…` | 研修 | arm64 | 1024MB | 20s | EventBridge 10分間隔 |
| 5 | `brightstar-hr-dev-webhook` | 人事 | arm64 | 256MB | 29s | Function URL |
| 6 | `brightstar-hr-dev-reconcile` | 人事 | arm64 | 256MB | 120s | EventBridge 毎日 0:00 JST |
| 7 | `brightstar-soumu-dev-webhook` | 総務 | arm64 | 512MB | 29s | Function URL |
| 8 | `brightstar-soumu-dev-reminder` | 総務 | arm64 | 256MB | 60s | EventBridge ＋ 他 Lambda からの非同期 Invoke |
| 9 | `brightstar-shain-dev-webhook` | 社員 | arm64 | 512MB | 29s | Function URL |
| 10 | `EkiCommute-dev-LineWebhookFn…` | 営業 | **x86_64** | 256MB | 300s | Function URL |
| 11 | `EkiCommute-dev-ApiFn…` | 営業 | **x86_64** | 256MB | 300s | Function URL（**AWS_IAM**） |

※ 上記のほか、CDK が自動生成する補助関数（`LogRetention*`・`CustomS3AutoDeleteObjects*`・`CustomCDKBucketDeployment*`）が存在する。設計対象外。

### メモリ・タイムアウトの設計根拠

| 値 | 対象 | 理由 |
|---|---|---|
| 256MB | 人事 webhook / 総務 reminder / 営業 | テキスト応答と DynamoDB 読み書きのみ。メモリ増は速度に寄与しない |
| 512MB | 総務 webhook / 社員 webhook | **一括DL の ZIP 生成をメモリ上で行う**（全社員の提出物を読み込む）ため余裕を持たせる |
| 1024MB | 研修 全関数 | Bedrock 呼び出し・RAG 検索を含み、**メモリ増＝CPU 増**でコールドスタートと推論待ちを短縮 |
| **29s** | 全 webhook | **LINE の応答タイムアウトが 30 秒**。これを超えると LINE 側で失敗扱いになるため、直前で切って自前のエラー応答を返す |
| 60s / 120s | 総務 reminder / 人事 reconcile | 全社員ループ＋LINE API 逐次呼び出し。人数に比例するため長め |
| 300s | 営業 | 外部サイト（駅探）のスクレイピングを含み、応答が遅い場合がある |

> **重い処理は同期で返さない**：催促送信・一斉送信・経路取得は、webhook が即座に ack を返し、実処理は**別 Lambda を非同期 Invoke**（`InvocationType=Event`）して push で結果を届ける。29 秒の壁を業務側に露出させないための共通パターン。

---

## 3. コード構成と vendoring

### 3.1 共通モジュールの配置

`common/` 配下（`authlib.py` 認証 / `assist.py` 多言語・QuickReply / `line.py` LINE API / `business.py` 業務 / `s3util.py` / `i18n.py` / `xlsx.py`）は、**各チャネルのディレクトリに実体をコピーして配置**している。

| 項目 | 設計 | 理由 |
|---|---|---|
| 共有方式 | **コピー（vendoring）**。Lambda Layer や共有パッケージにしない | ①Layer にすると 5 スタックが 1 つの Layer バージョンに結合し、1 チャネルの修正が全チャネルのデプロイを要求する ②チャネル独立（1 チャネルの障害・改修が他へ波及しない）を最優先した |
| 代償 | 同じ修正を複数箇所に適用する必要がある | 修正漏れを防ぐため、**正本を `jinji/lambda/common/` とし、他チャネルへ反映**する運用とする |

> ⚠️ **実装上の注意**：社員の提出処理が実際に動くのは `shain/lambda/jinji/common/` にコピーされた方である。`jinji/lambda/common/` だけ直しても社員チャネルの挙動は変わらない。

### 3.2 社員チャネルの名前空間分離

社員チャネル（`shain`）は研修と総務の「社員側」機能を 1 つの Lambda に同梱する。両者に同名の `common/` `handlers/` が存在するため、**`kenshu.*` / `jinji.*` の名前空間に分けて import する**ことで衝突を回避している。

```
shain/lambda/
├── handler.py          ルーター（モードで振り分け）
├── kenshu/{common,handlers}/
└── jinji/{common,handlers}/
```

I/O 層（LINE トークン・SSM パラメータ）は**社員チャネルのもの 1 本に寄せて**おり、vendoring 元のチャネルのトークンは使わない。

---

## 4. ソースとデプロイの同期

CDK の `Code.fromAsset` が**リポジトリのディレクトリをそのまま zip 化**して配布する。ビルド工程が挟まらないため、**リポジトリの内容 = デプロイされたコード**が常に成立する。

| チャネル | fromAsset が指す先 |
|---|---|
| 研修 | `kenshu/src` |
| 人事 | `jinji/lambda` |
| 総務 | `soumu/lambda` |
| 社員 | `shain/lambda` |
| 営業 | `eigyo/infra/build/lambda`（`build.py` が各所から集約。**このディレクトリは .gitignore**） |

### 検証手順（納品時に実施）

各関数の `Code.Location`（S3 署名付き URL）から zip を取得・展開し、リポジトリ側と **ファイル単位の md5 比較**を行う。営業のみ `build.py` のコピー規則で zip 内パス → git 管理下の元ファイルへ読み替えて突合する。

**2026-09-19 実施結果：全11関数・計122ファイルが一致（差分なし）**

| チャネル | 関数数 | 比較ファイル数 | 結果 |
|---|---|---|---|
| 研修 | 4 | 24 | 一致 |
| 人事 | 2 | 15 | 一致 |
| 総務 | 2 | 15 | 一致 |
| 社員 | 1 | 43 | 一致 |
| 営業 | 2 | 25 | 一致 |

---

## 5. 落とし穴

| # | 内容 | 対策 |
|---|---|---|
| 1 | **研修スタックの再デプロイで企業微信の設定が消える**。WeCom 系の値は Lambda 環境変数にしか無く、`-c` で渡さないと空文字で上書きされる | デプロイ前に**稼働中の Lambda 環境変数から値を読み戻して `-c` を組み立てる**。デプロイ後に before/after を突合して欠落が無いことを確認する（BS_INF-10 §4） |
| 2 | 人事スタックは `-c mailProofreadUrl` を渡さないと環境変数が空になる | 同上。必須 context は BS_INF-10 §3 の表を参照 |
| 3 | vendoring 先を直し忘れる（`jinji/lambda/common` だけ直して社員の挙動が変わらない） | 修正時は `shain/lambda/jinji/common/` の同名ファイルも必ず確認する |

---

## 6. 費用

Lambda は**リクエスト課金 + 実行時間課金**で、社内 100 名規模では**無料枠（月100万リクエスト・40万GB秒）に収まる**見込み。arm64 採用により単価はさらに 20% 低い。コストの主体は Lambda ではなく CloudWatch Logs と DynamoDB（BS_INF-09 §5 / BS_INF-03 §6）。

---

## 7. 関連文書

- 公開方法（Function URL / API Gateway）… [BS_INF-05](BS_INF-05_公開エンドポイント設計書.md)
- 権限 … [BS_INF-07 IAM設計書](BS_INF-07_IAM設計書.md)
- 定期実行 … [BS_INF-06 EventBridge設計書](BS_INF-06_EventBridge設計書.md)
- デプロイ … [BS_INF-10](BS_INF-10_CDK構成管理・デプロイ設計書.md)
