# BrightStar 統合LINEアシスタント DynamoDB設計書

| 項目 | 内容 |
|---|---|
| 文書番号 | BS_INF-03 |
| 文書名 | DynamoDB設計書 |
| 対象システム | BrightStar 統合LINEアシスタント（5チャネル） |
| 版数 | 初版 |
| 最終更新 | 2026-09-19 |
| 実機照合 | 2026-09-19（全15テーブル） |

---

## 1. 方針

| 項目 | 設計 | 理由 |
|---|---|---|
| DB 種別 | **DynamoDB のみ**（RDS を使わない） | アクセスパターンが「社員番号で引く」「ユーザーIDで引く」の単純なキー参照に限られ、結合・集計を必要としない。RDS は VPC・固定費・バックアップ運用を伴い、この規模では過剰 |
| 課金 | **全テーブル オンデマンド（PAY_PER_REQUEST）** | トラフィックが**月末に極端に偏る**（25日・28日の催促、月初の提出集中）。プロビジョンド方式ではピークに合わせた設定が平常時の無駄になる |
| 暗号化 | **AWS 管理キー（KMS）**。CMK は作らない | CMK の価値＝キーポリシーによるアクセス分離・独自ローテーション。現時点で該当要件がなく、キー毎の固定費と鍵運用事故（無効化＝復号不能）のリスクを負う理由がない |
| 削除ポリシー | `RemovalPolicy.DESTROY` | 検証段階の設計。**本番運用として継続するなら `RETAIN` へ変更すべき**（§7 課題2） |
| テーブル共有 | 社員名簿・認証は**スタックを跨いで名前参照で共有** | 5 チャネルで「同じ人」を一元管理するため（§4） |

---

## 2. テーブル一覧（2026-09-19 実測）

すべて `PAY_PER_REQUEST` / 暗号化有効。

| テーブル | 所有スタック | PK（+SK） | GSI | TTL | PITR | 用途 |
|---|---|---|---|---|---|---|
| `brightstar-hr-dev-roster` | 人事 | `empId` | — | — | **✅** | **社員名簿**。全チャネルの認証の基点 |
| `brightstar-hr-dev-auth` | 人事 | `pk`（`channel#userId`） | — | **✅** | — | 日次認証・言語設定（当日限り） |
| `brightstar-hr-dev-employees` | 人事 | `userId` | — | — | **✅** | LINEユーザー↔社員の紐付け状態 |
| `brightstar-hr-dev-submissions` | 人事 | `userId` + `sk` | GSI1 | — | **✅** | 提出記録 |
| `brightstar-soumu-dev-bookings` | 総務 | `bookingId` | — | — | — | 催促予約 |
| `brightstar-soumu-dev-broadcasts` | 総務 | `bcastId` | — | — | — | 配信バッチ・既読確認状況 |
| `brightstar-shain-dev-session` | 社員 | `userId` | — | — | — | 当日のモード（研修／総務） |
| `brightstar-kenshu-dev-courses` | 研修 | `courseId` | — | — | — | 研修コース |
| `brightstar-kenshu-dev-enrollments` | 研修 | `openid` + `courseId` | GSI1 | — | — | 受講申込 |
| `brightstar-kenshu-dev-groups` | 研修 | `courseId` + `groupId` | — | — | — | 分組 |
| `brightstar-kenshu-dev-students` | 研修 | `openid` | — | — | — | 受講者 |
| `brightstar-kenshu-dev-results` | 研修 | `openid` + `itemKey` | GSI1 | — | — | 受講結果 |
| `brightstar-kenshu-dev-knowledge` | 研修 | `docId` + `chunkId` | — | — | — | RAG 用ナレッジ |
| `EkiCommute-dev-StaffTable…` | 営業 | `staff_id` | — | — | — | 要員名簿 |
| `EkiCommute-dev-CacheTable…` | 営業 | `cacheKey` | — | **✅** | — | 経路検索キャッシュ |

> 営業の 2 テーブルのみ **AWS 所有キー**（他は AWS 管理 KMS キー）。AWS 所有キーは CloudTrail に鍵利用が記録されず、キーの存在をコンソールで確認できない。実害は小さいが**監査性は劣る**。

---

## 3. 主要テーブルのデータ設計

### 3.1 roster（社員名簿）— 本システムの中核

全チャネルの「この人は誰で、何をしてよいか」の唯一の判断材料。

| 属性 | 型 | 説明 |
|---|---|---|
| `empId` (PK) | S | 社員番号。実データは会社の 10 桁社員番号 |
| `name` | S | 氏名（漢字） |
| `department` | S | 所属部署 |
| `attribute` | S | 部署の細分類（例：日本籍／外国籍） |
| `role` | S | 主役割：`employee` / `hr` / `soumu` / `teacher` / `sales` |
| `roles` | S | 複数役割（CSV）。1 人に複数付与する場合に使用 |
| `admin` | BOOL | 一斉送信の実行権限 |
| `aliases` | S | 別名（カタカナ・ローマ字・簡体/繁体字）。氏名照合の表記ゆれ吸収用 |
| `lineUserId` | S | 紐付いた LINE ユーザーID（**1社員番号＝1アカウントの占用ロック**） |
| `blocked` | BOOL | bot のブロック／削除を検知したフラグ |
| `manualOk` | M | `{period}#{type}` → `{by, at, note}`。メール等で受領済みの手動「済」記録（監査情報つき） |

**認可の設計**：チャネルのゲートは `role` / `roles` を見て判定する（`authlib.has_role()`）。役割を持たない社員が総務チャネルを操作することはできない。個人情報（提出物）へのアクセスは**総務・人事の役割を持つ者に限定**される。

### 3.2 submissions（提出記録）

| 属性 | 説明 |
|---|---|
| `userId` (PK) | LINE ユーザーID |
| `sk` (SK) | `{period}#{type}`。その他経費のみ `{period}#other#{ts}`（月内複数件を許すため） |
| `gsi1pk` / `gsi1sk` | `{period}#{type}` / `userId`。**GSI1 = 月×種別で全社員を横断**するためのインデックス |
| `s3Key` / `fileName` / `submittedAt` | 実体の所在と受付情報 |

**GSI1 の設計理由**：総務の「未提出確認」は「2026-07 の勤務表を出した人の一覧」を必要とする。メインテーブルは userId が PK なので全件 Scan になってしまう。GSI1 で `period#type` を PK にすることで **Query 1 回**で取得できる。

### 3.3 auth（日次認証）

`pk = {channel}#{userId}` とし、**チャネルごとに独立した認証行**を持つ。`expireAt`（TTL）により**当日限りで自動失効**する。

TTL を使う理由：認証を永続化すると「退職者の LINE が生きたまま」になる。当日限りにすることで、翌日には必ず名簿照合が再実行され、名簿から消えた人は自動的に使えなくなる。

---

## 4. スタックを跨いだテーブル共有

```
BrightstarHr-dev（人事スタック）が作成・所有
  ├── roster ─────┬─→ 研修（brightstar-kenshu-dev）  が名前参照
  ├── auth ───────┤   総務（BrightstarSoumu-dev）     が名前参照
  ├── employees ──┤   社員（brightstar-shain-dev）    が名前参照
  └── submissions ┘   営業（EkiCommute-dev）          が名前参照
```

| 項目 | 設計 | 理由 |
|---|---|---|
| 参照方式 | **テーブル名の文字列で参照**（`Table.fromTableName`）。CloudFormation の Export/Import を使わない | Export/Import はスタック間に**削除順序の依存**を作る。人事スタックを更新するたびに参照側がロックされるのを避けたい |
| 前提 | テーブル名が固定であること | BS_INF-01 §1 で物理名を固定している理由がこれ |
| リスク | 人事スタックを destroy すると**他 4 チャネルが全滅**する | 運用上の注意事項として明記。`RemovalPolicy` の見直しとあわせて対応（§7 課題2） |

**同一 LINE Provider 前提**：5 チャネルは同じ LINE Provider 配下にあるため、同一人物の `userId` が全チャネルで一致する。これにより 1 チャネルで本人確認すれば他チャネルでも本人と判定できる。

---

## 5. バックアップ

| テーブル | PITR | 判断 |
|---|---|---|
| `roster` / `employees` / `submissions` | **有効** | 名簿の誤削除・提出記録の誤操作は業務影響が大きく、復旧手段が必要 |
| `auth` / `session` | 無効 | 当日限りの一時データ。失っても翌日の再認証で復旧する |
| `bookings` / `broadcasts` | 無効 | **要検討**（§7 課題1）。予約催促・既読確認は業務記録としての性質を持つ |
| 研修系 6 テーブル | 無効 | **要検討**（§7 課題1）。コース・受講申込は業務記録 |
| 営業 2 テーブル | 無効 | StaffTable は要員名簿＝業務記録。CacheTable は再取得可能なので不要 |

---

## 6. 費用

オンデマンド課金。実データ量は 2026-09 時点で全テーブル合計 **100 件未満**（roster 18・submissions 25 など）で、読み書き回数も社内 100 名規模では**無料枠圏内**。PITR を有効にしたテーブルはストレージ量に対する追加課金が発生するが、データ量が極小のため無視できる。

---

## 7. 既知の課題

| # | 深刻度 | 区分 | 内容 | 推奨対応 | 要否 |
|---|---|---|---|---|---|
| 1 | 中 | データ保護 | `bookings` / `broadcasts` / 研修6表 / 営業 StaffTable の **PITR 未設定**。誤削除・不正な一括更新から復旧できない | 業務記録の性質を持つテーブルに PITR を有効化（`pointInTimeRecovery: true`）。コストはデータ量比例で極小 | 将来対応 |
| 2 | **高** | 構成 | 全テーブルが `RemovalPolicy.DESTROY`。**スタック削除でデータが消える**。特に人事スタックは 4 チャネルの共有テーブルを保持しており、誤削除の影響が全社に及ぶ | 本番相当で運用を継続するなら `RETAIN` へ変更する。変更は CFN のメタデータのみでデータ影響なし | **早期対応推奨** |
| 3 | 低 | 監査 | 営業 2 テーブルのみ **AWS 所有キー**で、鍵利用が CloudTrail に残らない | 他と揃えて AWS 管理キーへ。**テーブル再作成が必要**なため、実害の小ささから対応しない判断も可 | 将来対応 |

---

## 8. 関連文書

- 暗号化方針の全体 … [BS_INF-08 SSM・暗号化設計書](BS_INF-08_SSM・暗号化設計書.md)
- テーブルへのアクセス権限 … [BS_INF-07 IAM設計書](BS_INF-07_IAM設計書.md)
- 命名規則 … [BS_INF-01](BS_INF-01_命名規則・タグ設計書.md)
