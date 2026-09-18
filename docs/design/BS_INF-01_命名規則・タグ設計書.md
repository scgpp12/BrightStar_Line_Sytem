# BrightStar 統合LINEアシスタント 命名規則・タグ設計書

| 項目 | 内容 |
|---|---|
| 文書番号 | BS_INF-01 |
| 文書名 | 命名規則・タグ設計書 |
| 対象システム | BrightStar 統合LINEアシスタント（5チャネル） |
| 版数 | 初版 |
| 最終更新 | 2026-09-19 |
| 実機照合 | 2026-09-19（ap-northeast-1 / アカウント 603319838936） |

---

## 1. 方針

**リソース名は「アプリ名 + ステージ + 用途」の 3 要素で構成し、物理名を明示指定する。**

理由：本システムは **5 つの独立スタックが同じ DynamoDB テーブル・S3 バケットを名前参照で共有**する（BS_INF-03 §4）。CDK の自動生成名（`EkiCommute-dev-StaffTable11B9C6C0-XMT2R0ORBPMC` のようなハッシュ付き）では、他スタックから名前で参照できない。したがって**共有対象は必ず物理名を固定**する。

共有しないリソース（IAM ロール等）は CDK の自動生成名で構わない。名前を固定すると別環境へ並行デプロイできなくなるため、**固定は必要なものに限る**。

---

## 2. 環境・アカウント

| 項目 | 値 |
|---|---|
| リージョン | `ap-northeast-1`（東京）— LINE 利用者・業務が日本国内のため |
| アカウント | 603319838936（単一アカウント） |
| ステージ | `dev` のみ（本番相当として運用中） |

> **現状の割り切り**：環境分離（dev/stg/prod）は行っていない。ステージ名はリソース名に含めてあるため、別ステージの並行デプロイは `-c stage=stg` で可能な設計にはなっている。実運用は `dev` 1 本。

---

## 3. 命名規則

### 3.1 基本形

```
{app}-{stage}-{resource}
```

| 要素 | 値 | 例 |
|---|---|---|
| `{app}` | `brightstar-kenshu` / `brightstar-hr` / `brightstar-soumu` / `brightstar-shain` / `eki-commute` | |
| `{stage}` | `dev` | |
| `{resource}` | 用途を表す小文字英字 | `roster` / `webhook` / `submissions` |

例：`brightstar-hr-dev-roster`、`brightstar-soumu-dev-webhook`

### 3.2 共通ルール

- 小文字 + ハイフン区切り（kebab-case）。
- 略語は使わない（`sub` ではなく `submissions`）。
- 連番は付けない（1 リソース 1 用途のため）。

### 3.3 例外（AWS 側の制約・CDK の都合）

| 対象 | 実際の名前 | 理由 |
|---|---|---|
| CloudFormation スタック（人事・総務・営業） | `BrightstarHr-dev` / `BrightstarSoumu-dev` / `EkiCommute-dev` | TypeScript CDK 側で PascalCase の Construct ID をそのままスタック名に使用したため |
| CloudFormation スタック（研修・社員） | `brightstar-kenshu-dev` / `brightstar-shain-dev` | Python CDK 側は基本形どおり |
| IAM ロール | `BrightstarHr-dev-WebhookFunctionServiceRoleA0DBD641-H8SgG36XAmgI` | CDK 自動生成。共有しないため固定不要 |
| S3 バケット | `brightstar-hr-dev-603319838936` | バケット名はグローバル一意が必要なため**アカウントIDを付加** |
| 営業の DynamoDB | `EkiCommute-dev-StaffTable11B9C6C0-XMT2R0ORBPMC` | CDK 自動生成名のまま（**基本形からの逸脱**・§5） |

---

## 4. タグ規則

全リソースにスタック単位で以下を付与する（`Tags.of(this).add(...)`）。

| タグキー | 値 | 目的 |
|---|---|---|
| `Project` | `brightstar-kenshu` / `brightstar-hr` / `brightstar-soumu` / `brightstar-shain` / `eki-commute` | Cost Explorer でのチャネル別コスト把握、削除時の一括特定 |
| `Stage` | `dev` | 環境識別 |
| `ManagedBy` | `cdk` | 手動作成リソースとの区別（手動作成物は削除時に取り残される） |

### 実機のタグ付与状況（2026-09-19）

| スタック | Project | Stage | ManagedBy | 備考 |
|---|---|---|---|---|
| `brightstar-kenshu-dev` | ✅ | ✅ | ✅ | 規則どおり |
| `BrightstarHr-dev` | ✅ | ✅ | ✅ | 規則どおり |
| `BrightstarSoumu-dev` | ✅ | ✅ | ✅ | 規則どおり |
| `brightstar-shain-dev` | ✅ | ✅ | ✅ | 規則どおり |
| `EkiCommute-dev` | ✅ | ❌ `Env=dev` | ❌ 無し | **規則からの逸脱**（§5） |

---

## 5. 既知の逸脱と対応方針

| # | 逸脱内容 | 影響 | 対応 |
|---|---|---|---|
| 1 | 営業スタックのタグが `Env=dev`（他は `Stage=dev`）、`ManagedBy` 無し | タグによる横断集計・一括抽出で営業だけ漏れる | **将来対応**。タグ変更は全リソース再作成を伴わないため、`cdk deploy` で追従可能 |
| 2 | 営業の DynamoDB テーブル名が CDK 自動生成 | 他スタックから名前参照できない。コンソールで用途が読み取れない | **対応しない**。営業のテーブルは他チャネルと共有しない設計のため実害が無く、**改名はテーブル再作成＝データ消失**を伴う |
| 3 | スタック名の大文字小文字が不統一 | 表示上のみ。実害なし | **対応しない**。スタック名変更＝スタック作り直し＝全リソース再作成となり、リスクに見合わない |

> いずれも「規則を後から決めた」ことに起因する。**新規リソースは基本形に従う**こととし、既存の逸脱は上表のとおり影響度で判断する。

---

## 6. 関連文書

- リソースの実体一覧 … [BS_INF-02 Lambda設計書](BS_INF-02_Lambda設計書.md) / [BS_INF-03 DynamoDB設計書](BS_INF-03_DynamoDB設計書.md) / [BS_INF-04 S3設計書](BS_INF-04_S3設計書.md)
- スタック構成とデプロイ … [BS_INF-10 CDK構成管理・デプロイ設計書](BS_INF-10_CDK構成管理・デプロイ設計書.md)
