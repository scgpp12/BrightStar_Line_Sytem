# BrightStar 統合LINEアシスタント IAM設計書

| 項目 | 内容 |
|---|---|
| 文書番号 | BS_INF-07 |
| 文書名 | IAM設計書 |
| 対象システム | BrightStar 統合LINEアシスタント（5チャネル） |
| 版数 | 初版 |
| 最終更新 | 2026-09-19 |
| 実機照合 | 2026-09-19（全ロール・インラインポリシー実査） |

---

## 1. 方針

| 項目 | 設計 | 理由 |
|---|---|---|
| ロールの粒度 | **Lambda 関数ごとに専用ロール**（CDK が自動生成） | 関数間で権限を共有しない。1 関数の権限追加が他へ波及しない |
| 権限付与 | CDK の `grant*` メソッド（`grantReadWriteData` 等）を使用 | リソース ARN の手書きミスを防ぎ、リソースとポリシーの対応が CDK 上で自明になる |
| 人間用ロール | **作らない**（IAM ユーザー `sonsik` の管理者権限で運用） | 運用者 1 名の小規模構成。**複数人運用に移行する場合は見直しが必要**（§5 課題3） |
| 権限境界 | 設定しない | 同上 |

> **重要な前提**：本システムのアクセス制御は **2 層**ある。
> - **アプリ層の認可**＝社員名簿の役割（`role`/`roles`）による機能ゲート。**個人情報の閲覧・ダウンロードはここで制御されている**。
> - **インフラ層の IAM**＝Lambda がどの AWS リソースを触れるか。
>
> 個人情報保護の実効的な統制はアプリ層にあり、そこは役割ベースで正しく実装されている（§3）。IAM 層は `grant*` の性質上やや粗い（§4）。

---

## 2. ロール一覧（2026-09-19 実測）

全ロールに共通して `AWSLambdaBasicExecutionRole`（CloudWatch Logs への書き込み）を付与。以下はインラインポリシーの内容。

| 関数 | DynamoDB | S3 | SSM | KMS | その他 |
|---|---|---|---|---|---|
| `brightstar-hr-dev-webhook` | 読み書き12アクション | 読み書き | GetParameter | Decrypt | — |
| `brightstar-hr-dev-reconcile` | 読み書き12アクション | — | GetParameter | Decrypt | — |
| `brightstar-soumu-dev-webhook` | 読み書き12アクション | 読み書き | GetParameter | Decrypt | `lambda:InvokeFunction` |
| `brightstar-soumu-dev-reminder` | 読み書き12アクション | — | GetParameter | Decrypt | — |
| `brightstar-shain-dev-webhook` | 読み書き12アクション | **明示6アクション** | GetParameter | Decrypt | `bedrock:InvokeModel` |
| `brightstar-kenshu-dev-LineWebhook` | 読み書き12アクション | — | GetParameter | Decrypt | `bedrock:InvokeModel` / `secretsmanager:GetSecretValue` |
| `EkiCommute-dev-LineWebhookFn` | 読み書き12アクション | 読み書き | GetParameter | Decrypt | `bedrock:InvokeModel` / `geo-places:*` / `lambda:InvokeFunction` |

**「読み書き12アクション」の内訳**（CDK `grantReadWriteData` が展開するもの）：
`BatchGetItem` / `BatchWriteItem` / `ConditionCheckItem` / `DeleteItem` / `DescribeTable` / `GetItem` / `GetRecords` / `GetShardIterator` / `PutItem` / `Query` / `Scan` / `UpdateItem`

### 個別権限の根拠

| 権限 | 付与先 | 用途 |
|---|---|---|
| `ssm:GetParameter` + `kms:Decrypt` | 全関数 | LINE チャネルシークレット／トークン（SecureString）の取得。SecureString は KMS 復号を伴うため両方が必要 |
| `lambda:InvokeFunction` | 総務 webhook / 営業 | 重い処理を別 Lambda へ**非同期委譲**するため（[BS_INF-05](BS_INF-05_公開エンドポイント設計書.md) §5） |
| `bedrock:InvokeModel` | 社員・研修・営業 | 研修の RAG Q&A、営業の住所正規化。**現状 `BEDROCK_ENABLED=false` で未使用**だが権限は付いている |
| `secretsmanager:GetSecretValue` | 研修 | Zoom API 認証情報 |
| `geo-places:Geocode / SearchText / SearchNearby` | 営業 | 住所 → 最寄駅の変換 |

---

## 3. アプリ層の認可（個人情報保護の実効統制）

本システムが扱う個人情報：**氏名・社員番号・所属部署・国籍等の属性・LINE ユーザーID・勤務実績・通勤経路・住所**。マイナンバー・銀行口座・在留カードは**扱わない**。

| 操作 | 必要な役割 | 実装 |
|---|---|---|
| 自分の提出・自分の履歴閲覧 | `employee`（全社員） | 本人の `userId` に紐づくデータのみ参照 |
| 全社員の提出状況・未提出一覧・CSV 出力 | **`hr` または `soumu`** | 総務チャネルの認証ゲート |
| 提出物の一括DL・個別DL・削除 | **`hr` または `soumu`** | 同上 |
| 社員名簿の追加・変更・削除 | **`hr`** | 人事チャネルの認証ゲート |
| 一斉送信 | **`admin` 属性を持つ者のみ** | 役割に加えた追加チェック |
| 研修のコース作成・公開 | `teacher` | 研修チャネルの認証ゲート |
| 要員名簿・通勤コスト比較 | `sales` | 営業チャネルの認証ゲート |

**評価**：個人情報を横断的に閲覧・取得できる操作（一覧・CSV・一括DL・削除）は**すべて総務／人事の役割に限定**されており、一般社員・営業・講師は自分の情報以外に到達できない。「ログイン済みなら全ロール可」にはなっていない。

**認証の失効**：認証行は TTL により**当日限りで失効**する。翌日は必ず名簿照合が再実行されるため、**名簿から削除された退職者は翌日には利用不能**になる。加えて日次リコンサイル（[BS_INF-06](BS_INF-06_EventBridge設計書.md)）が到達不可のアカウントの紐付けを解除する。

---

## 4. IAM 層の権限評価

| 観点 | 評価 |
|---|---|
| 関数ごとに専用ロール | ✅ できている |
| 不要なサービスへの権限が無い | ✅ 概ねできている（各関数が実際に使うサービスのみ） |
| アクションの絞り込み | ⚠️ **粗い**。`grantReadWriteData` により、読むだけの関数にも `DeleteItem`・`Scan` が付く |
| リソースの絞り込み | ⚠️ **粗い**。参照する全テーブルに一律で同じ12アクションが付く |

**具体例**：営業チャネルの Lambda は社員名簿（roster）を「役割を確認するために読む」だけだが、実際には **roster に対する `DeleteItem` 権限**を持っている。人事チャネルの reconcile も同様。

これは CDK の `grantReadWriteData` を使った結果であり、意図的に広げたものではない。**アプリ層で役割ゲートが効いているため実際の業務操作としては到達不能**だが、コードの不具合や将来の改修で誤って削除処理を書いた場合、IAM が最後の防波堤にならない。

社員チャネルのみ S3 権限を明示列挙（`GetObject`/`PutObject`/`DeleteObject`/`ListBucket`/`PutObjectTagging`/`GetObjectTagging`）しており、**この書き方が本来あるべき形**である。

---

## 5. 指摘事項

```
[深刻度: 中] [区分: 認可]
内容 : 読み取りしか行わない Lambda にも DynamoDB の DeleteItem / BatchWriteItem 権限が付いている。
       特に営業・研修の Lambda が社員名簿（roster）を削除できる状態。
根拠 : 最小権限の原則から逸脱。アプリ層の役割ゲートを回避する不具合（例：誤ったハンドラ登録、
       想定外の入力によるコードパス）が入った場合、IAM が防御層として機能しない。
       roster は全チャネルの認証基盤であり、破壊されると全社で bot が使用不能になる。
推奨対応: 参照のみの関数は grantReadData に変更する。読み書きが必要な関数も、
       社員チャネルの S3 権限と同様にアクションを明示列挙する。
ブロッカー要否: 将来対応（アプリ層の統制が効いているため即時のリスクは低い）
```

```
[深刻度: 低] [区分: 構成]
内容 : bedrock:InvokeModel が社員・研修・営業に付与されているが、BEDROCK_ENABLED=false で機能は未使用。
根拠 : 使っていない権限は攻撃面を広げる。またリソースが "*" 指定。
推奨対応: 機能を有効化するまで権限を外す。有効化時は foundation-model と inference-profile の
       ARN を明示する（東京リージョンの Claude は inference-profile 必須）。
ブロッカー要否: 将来対応
```

```
[深刻度: 中] [区分: 構成]
内容 : 人間（運用者）用の IAM ロールが定義されておらず、管理者権限の IAM ユーザーで直接運用している。
根拠 : 運用者が 1 名のうちは実害が小さいが、複数人運用・委託先参加の段階で
       「誰が何をしたか」の追跡と権限分離ができない。個人情報を保持するシステムとして、
       アクセス権者の限定は個人情報保護法上の安全管理措置に該当する。
推奨対応: 運用者ロール（読み取り中心）とデプロイロール（CDK 実行）を分離し、
       IAM ユーザーは AssumeRole 経由に切り替える。
ブロッカー要否: 複数人運用へ移行する前に必須 / 現状（1名運用）では将来対応
```

```
[深刻度: 低] [区分: 監査]
内容 : CloudTrail のデータイベント（S3 オブジェクトアクセス・DynamoDB 項目アクセス）が未設定。
根拠 : 提出物（個人情報）に誰がいつアクセスしたかの記録が残らない。
       管理イベントは既定で記録されるが、オブジェクト単位のアクセスは対象外。
推奨対応: S3 バケット hr/ プレフィックスのデータイベントを CloudTrail で記録する。
       費用はイベント数課金のため、本規模では軽微。
ブロッカー要否: 将来対応
```

---

## 6. 費用

IAM 自体は無料。CloudTrail データイベントを有効化する場合のみ、記録イベント数に応じた課金が発生する。

---

## 7. 関連文書

- 認証情報の保管と暗号鍵 … [BS_INF-08](BS_INF-08_SSM・暗号化設計書.md)
- 監査ログ … [BS_INF-09](BS_INF-09_監視・ログ設計書.md)
- 役割ゲートの機能仕様 … [11 機能仕様書](../11_機能仕様書.md) §1
