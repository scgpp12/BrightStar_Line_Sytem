# BrightStar 統合LINEアシスタント CDK構成管理・デプロイ設計書

| 項目 | 内容 |
|---|---|
| 文書番号 | BS_INF-10 |
| 文書名 | CDK構成管理・デプロイ設計書 |
| 対象システム | BrightStar 統合LINEアシスタント（5チャネル） |
| 版数 | 初版 |
| 最終更新 | 2026-09-19 |
| 実機照合 | 2026-09-19 |

---

## 1. 方針

| 項目 | 設計 | 理由 |
|---|---|---|
| IaC | **AWS CDK v2**（`aws-cdk-lib ^2.150.0`） | 手動作成リソースを作らない。全構成をコードで再現可能にする |
| スタック分割 | **チャネルごとに独立したスタック・独立した CDK アプリ** | 1 チャネルの改修が他チャネルのデプロイを要求しない。障害の波及も防ぐ |
| 言語 | TypeScript（人事・総務・営業）／ Python（研修・社員） | 各チャネルが別時期に別プロジェクトとして作られた経緯。**統一していない**（§7 課題1） |
| コード同梱 | `Code.fromAsset` でリポジトリのディレクトリをそのまま zip 化 | ビルド工程を挟まないため、**リポジトリ = デプロイ内容**が常に成立する（§5） |
| 機密の注入 | SSM SecureString のパラメータ**名**のみ CDK に持たせる | 値を CDK・テンプレート・context に置かない（[BS_INF-08](BS_INF-08_SSM・暗号化設計書.md) §1） |
| bootstrap | `ap-northeast-1` に実施済み（`CDKToolkit`） | 再実行不要 |

---

## 2. スタック構成

| スタック名 | チャネル | 言語 | ディレクトリ | 作成する主なリソース |
|---|---|---|---|---|
| `brightstar-kenshu-dev` | 研修 | Python | `kenshu/cdk` | Lambda×4 / API GW / DynamoDB×6 / EventBridge×1 |
| `BrightstarHr-dev` | 人事 | TypeScript | `jinji/cdk` | Lambda×2 / Function URL / **DynamoDB×4（共有）** / **S3（共有）** / EventBridge×1 |
| `EkiCommute-dev` | 営業 | TypeScript | `eigyo/infra` | Lambda×2 / Function URL×2 / DynamoDB×2 / Layer |
| `BrightstarSoumu-dev` | 総務 | TypeScript | `soumu/cdk` | Lambda×2 / Function URL / DynamoDB×2 / EventBridge×2 |
| `brightstar-shain-dev` | 社員 | Python | `shain/cdk` | Lambda×1 / Function URL / DynamoDB×1 |

### 依存関係

```
BrightstarHr-dev（人事）
  └─ roster / auth / employees / submissions / S3バケット を作成
       ↑ 他の 4 スタックが「テーブル名の文字列」で参照（Export/Import は使わない）
```

| 項目 | 内容 |
|---|---|
| **初回デプロイ順序** | **人事を最初**にデプロイする（共有テーブル・バケットを作るため）。以降は任意 |
| 更新時の順序 | 制約なし。各スタックを独立に更新できる |
| **削除時の注意** | **人事スタックを削除すると他4チャネルが全滅**する。全テーブルが `RemovalPolicy.DESTROY` のため、データも消える（[BS_INF-03](BS_INF-03_DynamoDB設計書.md) §7 課題2） |

Export/Import を使わない理由：スタック間に削除順序の依存が生まれ、人事スタックを更新するたびに参照側がロックされるため。名前参照なら疎結合を保てる。

---

## 3. デプロイコマンドと必須 context

```bash
# 研修（Python CDK）— WeCom 系の context 必須（§4）
cd kenshu/cdk && npx cdk deploy brightstar-kenshu-dev --require-approval never -c weComCorpId=… （18項目）

# 人事（TypeScript CDK）
cd jinji/cdk && npm install && npx cdk deploy BrightstarHr-dev --require-approval never \
  -c masterHrPrefix=sonsik -c mailProofreadUrl="https://sons02-relay.tail5a0084.ts.net:8443/"

# 総務
cd soumu/cdk && npm install && npx cdk deploy BrightstarSoumu-dev --require-approval never \
  -c masterHrPrefix=sonsik

# 社員（context 不要）
cd shain/cdk && npx cdk deploy brightstar-shain-dev --require-approval never

# 営業（Layer をビルドしてから）
cd eigyo/infra && npm install && python build.py && npx cdk deploy EkiCommute-dev --require-approval never
```

### ⚠️ context を渡し忘れるとどうなるか

CDK は `tryGetContext` の戻りが `undefined` のとき**既定値（多くは空文字）**を使う。**エラーにならず、環境変数が空で上書きされる**。

| スタック | 必須 context | 渡し忘れた場合 |
|---|---|---|
| 研修 | `weComCorpId` / `weComToken` / `weComAesKey` / `weComAgentId` / `weComSecret` / `weComRelayUrl` / `weComRelayAuth` / `weComKfOpenKfId` / `teacherOpenids` / `teacherSignupCode` ほか計18 | **企業微信連携が無言で停止**。講師の signup code も失われる |
| 人事 | `masterHrPrefix` / `mailProofreadUrl` | メール校正ツールへのリンクが消える |
| 総務 | `masterHrPrefix` | マスター権限判定が効かなくなる |
| 社員・営業 | なし | — |

---

## 4. 研修スタックの安全な再デプロイ手順

研修の設定値は **Lambda 環境変数にしか存在しない**（SSM 化されていない — [BS_INF-08](BS_INF-08_SSM・暗号化設計書.md) §4）。そのため、**稼働中の Lambda から値を読み戻して context を組み立てる**。

```powershell
# 1. 現在の環境変数を取得
$FN = "brightstar-kenshu-dev-LineWebhookFunctionE63D95CF-1qfCxM64IodC"
$before = aws lambda get-function-configuration --function-name $FN --region ap-northeast-1 `
            --query "Environment.Variables" --output json | ConvertFrom-Json

# 2. ctxキー → env変数名 の対応表から -c 引数を生成（値をスクリプトに書かない）
#    weComCorpId→WECOM_CORP_ID, weComToken→WECOM_TOKEN, ... 計18項目

# 3. cdk deploy @ctxArgs

# 4. デプロイ後に before/after を突合し、失われた変数が無いことを確認
```

**手順の要点**：値をスクリプト・シェル履歴に書かず、AWS → CLI の経路だけで流す。デプロイ後の**突合確認を必ず行う**（2026-09-19 実施時は 34 変数すべて保持を確認）。

> この手順は SSM 化（[BS_INF-08](BS_INF-08_SSM・暗号化設計書.md) §4）により不要になる。恒久対策はそちら。

---

## 5. ソースとデプロイの同期検証

`Code.fromAsset` の性質上リポジトリ＝デプロイ内容だが、**納品時に機械的に検証**する。

### 手順

1. 各 Lambda の `Code.Location`（S3 署名付き URL）から配布 zip を取得・展開
2. リポジトリ側の対応ソースと **ファイル単位の md5 比較**
3. 営業のみ `build.py` のコピー規則で「zip 内パス → git 管理下の元ファイル」へ読み替えて突合
   （`eigyo/infra/build/` は `.gitignore` のため、build 成果物と比較しても意味がない）

### 2026-09-19 実施結果：**全11関数・122ファイルが一致（差分なし）**

| チャネル | 関数数 | ファイル数 | 結果 |
|---|---|---|---|
| 研修 | 4 | 24 | 一致 |
| 人事 | 2 | 15 | 一致 |
| 総務 | 2 | 15 | 一致 |
| 社員 | 1 | 43 | 一致 |
| 営業 | 2 | 25 | 一致 |

リポジトリ（`main`）は同時点で未コミット変更・未プッシュコミットともに無し。

---

## 6. 落とし穴集

| # | 内容 | 対策 |
|---|---|---|
| 1 | **研修の再デプロイで企業微信設定が消える** | §4 の手順を必ず使う |
| 2 | **Function URL は削除・再作成で URL が変わる** | 変わったら LINE Developers 側の Webhook URL を手で更新する。通常のスタック更新では変わらない |
| 3 | `logRetention` が CDK v2 で deprecated | 既存ログを保持するため意図的に使用。v3 移行時に `logGroup` 方式へ（[BS_INF-09](BS_INF-09_監視・ログ設計書.md) §2） |
| 4 | **git-bash から `/` 始まりの引数を渡すと Windows パスに書き換えられる** | SSM パラメータ名（`/brightstar-hr/...`）・ロググループ名（`/aws/lambda/...`）を扱う AWS CLI は **PowerShell で実行**する |
| 5 | Windows コンソールが GBK で CJK 出力が壊れる | `PYTHONUTF8=1` を付ける、または `chcp 65001` |
| 6 | vendoring 先の直し忘れ | 社員チャネルの提出処理は `shain/lambda/jinji/common/` が実体。`jinji/lambda/common/` だけ直しても反映されない |
| 7 | 営業は `python build.py` を先に実行しないと古いコードがデプロイされる | デプロイ手順に含める（§3） |

---

## 7. 既知の課題

| # | 深刻度 | 内容 | 推奨対応 | 要否 |
|---|---|---|---|---|
| 1 | 低 | CDK の言語が TypeScript / Python で混在 | 実害は小さい（各スタックが独立しているため）。統一は書き直しコストに見合わない | 対応不要 |
| 2 | 中 | **CI/CD が無い**。デプロイは手元の PC から手動実行 | 手順の属人化・デプロイ漏れのリスク。GitHub Actions + OIDC で `cdk deploy` を自動化できる | 将来対応 |
| 3 | 中 | `cdk diff` を必ず確認する運用が明文化されていない | デプロイ前の `cdk diff` を手順に組み込む | 早期対応（運用ルール） |
| 4 | 低 | ステージが `dev` のみ。本番相当を dev で運用している | 環境分離が必要になった時点で `-c stage=prod` で並行構築できる設計にはなっている | 将来対応 |

---

## 8. 関連文書

- 全設計書の索引 … [README](README.md)
- リソース実体 … [BS_INF-02](BS_INF-02_Lambda設計書.md) / [BS_INF-03](BS_INF-03_DynamoDB設計書.md) / [BS_INF-04](BS_INF-04_S3設計書.md)
- 機密の注入 … [BS_INF-08](BS_INF-08_SSM・暗号化設計書.md)
