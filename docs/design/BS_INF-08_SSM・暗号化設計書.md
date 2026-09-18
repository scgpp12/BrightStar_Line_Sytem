# BrightStar 統合LINEアシスタント SSM Parameter Store・暗号化設計書

| 項目 | 内容 |
|---|---|
| 文書番号 | BS_INF-08 |
| 文書名 | SSM Parameter Store・暗号化設計書 |
| 対象システム | BrightStar 統合LINEアシスタント（5チャネル） |
| 版数 | 初版 |
| 最終更新 | 2026-09-19 |
| 実機照合 | 2026-09-19 |

---

## 1. 機密情報の保管方針

| 項目 | 設計 | 理由 |
|---|---|---|
| 保管先 | **SSM Parameter Store（SecureString）** | Secrets Manager はローテーション機能を持つが**パラメータ毎に月額固定費**が発生する。LINE のチャネルシークレット／トークンは自動ローテーション非対応（LINE 側の仕様）で、Secrets Manager の主機能を活かせない。Parameter Store の SecureString は無料で、必要な機能（KMS 暗号化・IAM 制御）は満たす |
| 値の投入 | **デプロイとは別に手動で 1 回投入**。CDK はパラメータ名のみを参照 | CDK のコードにもコンテキストにも値を残さない。`cdk.context.json` や CloudFormation テンプレートに秘密が焼き込まれる事故を構造的に防ぐ |
| ソース管理 | 値は**リポジトリ・設計書・記憶ファイルのいずれにも書かない** | `.gitignore` で `*.env` を除外。設計書には**パラメータ名のみ**記載する |
| 例外 | Zoom の認証情報のみ Secrets Manager | 研修チャネルの既存実装を踏襲 |

---

## 2. パラメータ一覧（2026-09-19 実測・名前のみ）

すべて **SecureString**。

| パラメータ名 | 用途 |
|---|---|
| `/brightstar-kenshu/dev/line/secret` | 研修チャネルの署名検証用シークレット |
| `/brightstar-kenshu/dev/line/token` | 研修チャネルのアクセストークン |
| `/brightstar-hr/dev/line/secret` | 人事チャネル |
| `/brightstar-hr/dev/line/token` | 人事チャネル |
| `/brightstar-soumu/dev/line/secret` | 総務チャネル |
| `/brightstar-soumu/dev/line/token` | 総務チャネル |
| `/brightstar-shain/dev/line/secret` | 社員チャネル |
| `/brightstar-shain/dev/line/token` | 社員チャネル |
| `/eki-commute/dev/line/channel-secret` | 営業チャネル |
| `/eki-commute/dev/line/channel-access-token` | 営業チャネル |

命名は `/{app}/{stage}/line/{secret|token}`（営業のみ既存実装の都合で `channel-` 接頭辞）。

### シークレットの二重用途

LINE チャネルシークレットは、**署名検証**に加えて**ダウンロード用短縮 URL の HMAC 署名鍵**にも流用している（[BS_INF-04](BS_INF-04_S3設計書.md) §4、[BS_INF-05](BS_INF-05_公開エンドポイント設計書.md) §4）。

```
[深刻度: 低] [区分: 鍵管理]
内容 : 1 つの鍵を「LINE 署名検証」と「自前の URL 署名」の 2 用途で共用している。
根拠 : LINE 側の都合でチャネルシークレットを再発行すると、発行済みのダウンロードリンクが
       一斉に無効になる。逆に URL 署名方式を変えたいときに LINE 側へ影響が出る。
       鍵の用途分離（1鍵1用途）の原則から外れる。
推奨対応: URL 署名用に専用のパラメータ（例 /brightstar-soumu/dev/dl/signing-key）を追加する。
       既存リンクは exports/ が 3 日で消えるため、移行の影響は軽微。
ブロッカー要否: 将来対応
```

---

## 3. 暗号鍵（KMS）方針

**AWS 管理キー（サービス既定の暗号化）で運用し、CMK（顧客管理キー）は作らない。**

理由：CMK の価値はキーポリシーによるアクセス分離・独自のローテーション周期・クロスアカウント共有だが、本システムは**単一アカウント・単一ステージ・運用者1名**でいずれにも該当しない。CMK はキー毎の固定費と、鍵の無効化事故＝データ復号不能というリスクを伴う。要件が出た時点で導入する。

### サービス別の暗号化状況（2026-09-19 実測）

| サービス | 暗号化方式 | キー | 出典 |
|---|---|---|---|
| SSM Parameter Store | SecureString | `aws/ssm`（AWS 管理キー） | 本書 §2 |
| DynamoDB（brightstar 系13表） | 保存時暗号化 | **AWS 管理キー** | [BS_INF-03](BS_INF-03_DynamoDB設計書.md) §2 |
| DynamoDB（営業2表） | 保存時暗号化 | **AWS 所有キー**（監査性が劣る） | [BS_INF-03](BS_INF-03_DynamoDB設計書.md) §2 |
| S3（`brightstar-hr-dev-…`） | SSE-S3（AES-256） | S3 管理 | [BS_INF-04](BS_INF-04_S3設計書.md) §2 |
| Secrets Manager（Zoom） | 既定暗号化 | `aws/secretsmanager` | 研修チャネル |
| CloudWatch Logs | 既定暗号化 | サービス管理 | [BS_INF-09](BS_INF-09_監視・ログ設計書.md) |
| 転送中 | TLS（HTTPS）のみ | — | LINE API・AWS SDK とも HTTPS |

### CMK へ移行する条件（いずれかが発生したら本書改訂）

1. 監査（ISO 27001 等）で鍵管理・ローテーション周期の明示を求められた場合。
2. 複数アカウント構成へ移行し、アカウント間でデータ共有が必要になった場合。
3. マイナンバー・銀行口座など、**より機微な情報**を扱う範囲へ拡張する場合。
   > 現状の取扱情報は氏名・所属・勤務実績・通勤経路までで、特定個人情報は扱っていない。

---

## 4. 既知の課題

```
[深刻度: 高] [区分: 機微情報 / 構成]
内容 : 研修チャネルの企業微信（WeCom）認証情報が Lambda の環境変数に平文で保持されている。
       対象: WECOM_SECRET / WECOM_AES_KEY / WECOM_TOKEN / WECOM_RELAY_AUTH。
根拠 : Lambda の GetFunctionConfiguration 権限を持つ者・CloudFormation テンプレートの閲覧者が
       値を平文で読める。SSM SecureString に統一するという本システムの方針からも逸脱している。
       また CDK デプロイ時に -c で渡すため、シェル履歴・CI ログに残る経路がある。
推奨対応: SSM SecureString（/brightstar-kenshu/dev/wecom/*）へ移し、Lambda は起動時に取得する
       （LINE の secret/token と同じパターンが既に実装済みなので流用できる）。
       移行後は企業微信側で認証情報を再発行し、平文で流通した旧値を無効化する。
ブロッカー要否: 企業微信連携を継続するなら早期対応。LINE 側の運用には影響しない。
```

```
[深刻度: 中] [区分: 鍵管理 / 運用]
内容 : 研修スタックの再デプロイ時、上記の値を -c で渡し忘れると環境変数が空で上書きされ、
       企業微信連携が無言で停止する。
根拠 : CDK は context 未指定時に空文字を既定値としているため、エラーにならず機能だけが止まる。
推奨対応: SSM 化（上記）により構造的に解消する。それまでは、稼働中 Lambda の環境変数から
       値を読み戻して -c を組み立てる手順を必ず使う（BS_INF-10 §4）。
ブロッカー要否: 手順で回避中（運用ルールとして明文化済み）
```

---

## 5. 費用

- SSM Parameter Store（Standard + SecureString）：**無料**
- AWS 管理キーによる暗号化：**無料**（KMS API 呼び出しは Lambda 起動時のパラメータ取得のみで、無料枠内）
- Secrets Manager（Zoom 用 1 件）：月額約 $0.4

CMK を採用した場合はキー毎に月額 $1 + API 従量が追加される。

---

## 6. 関連文書

- 権限（誰がパラメータを読めるか）… [BS_INF-07 IAM設計書](BS_INF-07_IAM設計書.md)
- 署名付き URL の仕組み … [BS_INF-04 S3設計書](BS_INF-04_S3設計書.md) §4
- デプロイ時の context 指定 … [BS_INF-10](BS_INF-10_CDK構成管理・デプロイ設計書.md) §3
