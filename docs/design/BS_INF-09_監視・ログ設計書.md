# BrightStar 統合LINEアシスタント 監視・ログ設計書

| 項目 | 内容 |
|---|---|
| 文書番号 | BS_INF-09 |
| 文書名 | 監視・ログ設計書 |
| 対象システム | BrightStar 統合LINEアシスタント（5チャネル） |
| 版数 | 初版 |
| 最終更新 | 2026-09-19 |
| 実機照合 | 2026-09-19 |

---

## 1. 方針

| 項目 | 設計 | 理由 |
|---|---|---|
| ログ出力 | Lambda の標準出力 → CloudWatch Logs | 追加のエージェント・転送基盤を持たない |
| 保持期間 | **30日**（営業チャネルのみ 14日） | ログに**氏名・LINE ユーザーID が含まれる**（§3）ため無期限保持にしない。一方、業務が月次サイクル（25日・28日の催促）であるため、**直近1サイクルを追える長さ**が必要。この2条件の交点が 30 日 |
| 集約 | 各 Lambda のロググループのみ。集約基盤は持たない | 関数が 11 本と少なく、CloudWatch Logs Insights で横断検索できる |
| メトリクス監視 | **現状ほぼ未実装**（§4） | — |

---

## 2. ロググループと保持期間（2026-09-19 実測）

| ロググループ | 保持期間 |
|---|---|
| `/aws/lambda/brightstar-hr-dev-webhook` | 30日 |
| `/aws/lambda/brightstar-hr-dev-reconcile` | 30日 |
| `/aws/lambda/brightstar-soumu-dev-webhook` | 30日 |
| `/aws/lambda/brightstar-soumu-dev-reminder` | 30日 |
| `/aws/lambda/brightstar-shain-dev-webhook` | 30日 |
| `/aws/lambda/brightstar-kenshu-dev-*`（4本） | 30日 |
| `/aws/lambda/EkiCommute-dev-*`（2本） | 14日 |
| CDK 補助関数（LogRetention / CustomResource 等） | 1日 または 30日 |

**無期限保持のロググループは 0 件**（19 件すべてに保持期間を設定済み）。

### 実装（CDK）

```typescript
// TypeScript CDK（人事・総務）
new lambda.Function(this, "WebhookFunction", {
  ...
  // ログに氏名等が出るため無期限保持にしない（月次業務1周期を追える30日）
  logRetention: logs.RetentionDays.ONE_MONTH,
});
```

```python
# Python CDK（研修・社員）
lambda_.Function(
    ...,
    log_retention=logs.RetentionDays.ONE_MONTH,
)
```

> **落とし穴**：`logRetention` は CDK v2 で **deprecated**（v3 で削除予定）。非推奨でない書き方は `logGroup` に明示的な `LogGroup` を渡す方式だが、**既存のロググループが存在する状態でこれを使うと「already exists」でデプロイが失敗**する。移行するにはロググループを一旦削除する＝既存ログを失う。本システムは**既存ログを保持したかったため `logRetention` を選択**した。CDK v3 移行時に、ログを捨ててよいタイミングで `logGroup` 方式へ切り替える。

### 2026-09-19 に実施した是正

| 内容 | 件数 |
|---|---|
| 無期限保持 → 30日へ変更（CDK に `logRetention` 追加・4スタック再デプロイ） | 9 ロググループ |
| CDK 補助関数のロググループに 30日を設定（CLI） | 3 ロググループ |
| **旧スタック由来の孤立ロググループを削除** | 5 ロググループ（計 791KB・最終ログ 2026-06） |

削除したもの：`brightstar-dev-line-webhook` / `brightstar-dev-reminder` / `brightstar-dev-web` / `brightstar-dev-webhook`（削除済みスタックの残骸）、`brightstar-hr-dev-reminder`（機能が総務チャネルへ移管され関数が消滅）。いずれも**対応する Lambda 関数が存在しないこと**を確認のうえ削除した。

---

## 3. ログに出力される情報

| 出力箇所 | 内容 | 個人情報 |
|---|---|---|
| `[UNFOLLOW] {userId} name={氏名}` | bot のブロック／削除検知 | **氏名・LINE ユーザーID** |
| `[RECONCILE] cleared empId={社員番号} name={氏名}` | 日次リコンサイルでの紐付け解除 | **氏名・社員番号** |
| `push fail: {userId} {エラー}` | LINE push の失敗 | **LINE ユーザーID** |
| `reminder sent=N period=… type=…` | 催促の送信件数 | なし（統計のみ） |
| `route error: {例外}` | ハンドラ内例外 | 例外内容による |

```
[深刻度: 中] [区分: 個人情報]
内容 : CloudWatch Logs に氏名・社員番号・LINE ユーザーID が平文で出力されている。
根拠 : 個人情報保護法の安全管理措置上、個人データは利用目的の範囲で必要最小限に扱う必要がある。
       障害調査目的でログに氏名を残す必然性は低く（社員番号または userId の下4桁で追跡可能）、
       ログ閲覧権限を持つ者すべてが氏名を閲覧できる状態になる。
推奨対応: ログ出力を識別子のみに変更する（name= を落とす、userId は末尾数桁にマスクする）。
       保持期間 30 日の設定により露出期間は限定済みだが、出力自体を減らすのが本筋。
ブロッカー要否: 将来対応（保持期間の設定により当面のリスクは低減済み）
```

---

## 4. メトリクス監視

### 現状：**CloudWatch アラーム 0 件**

```
[深刻度: 高] [区分: 運用]
内容 : アラームが 1 つも設定されておらず、障害を検知する手段が無い。
根拠 : 本システムの中核機能である「月末の自動催促」は月2回しか動かない。
       reminder Lambda が失敗しても誰にも通知されず、
       「催促が来ないまま締切を過ぎた」ことに人が気付くまで分からない。
       同様に、webhook が全件エラーになっても LINE 利用者が個別に報告するまで検知できない。
推奨対応: 下表の最小構成のアラームを設定し、SNS で管理者へ通知する。
ブロッカー要否: 早期対応（本番相当で運用しているため）
```

### 推奨する最小構成

| # | 対象 | メトリクス | しきい値 | 意味 |
|---|---|---|---|---|
| 1 | `brightstar-soumu-dev-reminder` | `Errors` | 1回以上 / 5分 | **催促が送れていない**＝業務が止まる。最優先 |
| 2 | `brightstar-hr-dev-reconcile` | `Errors` | 1回以上 / 日次 | 名簿の到達性点検が失敗 |
| 3 | 全 webhook（3本） | `Errors` | 5回以上 / 5分 | 恒常的なエラー（単発は LINE 側の一時障害もあるため閾値を持たせる） |
| 4 | 全 webhook | `Throttles` | 1回以上 | 同時実行数の上限に当たっている |
| 5 | `brightstar-soumu-dev-reminder` | `Invocations` | **25日・26日に 0 件** | 定時催促が「起動すらしていない」ことの検知（EventBridge 側の異常） |

> #5 は「動かなかったこと」を検知する設計。Errors だけでは、EventBridge ルールが無効化された場合や Lambda が呼ばれなかった場合を捕まえられない。

### 通知先

SNS トピック 1 本を作り、管理者のメールアドレスを購読させるのが最小構成。
LINE への通知（総務チャネルへ push）も技術的には可能だが、**LINE 自体の障害時に届かない**ため、監視通知は LINE と独立した経路にすべき。

---

## 5. 費用

| 項目 | 現状 |
|---|---|
| CloudWatch Logs 取り込み | 月あたり数十MB 程度（最大のログは研修 reminder の 7MB／30日） |
| CloudWatch Logs 保管 | 30日保持により**上限が定まった**。無期限だった従来は累積し続ける構造だった |
| アラーム | 未設定（設定しても 10 個までは無料枠） |

保持期間の設定は、個人情報の観点だけでなく**保管コストの上限を確定させる**効果もある。

---

## 6. 既知の課題（まとめ）

| # | 深刻度 | 内容 | 要否 |
|---|---|---|---|
| 1 | **高** | CloudWatch アラームが 0 件。催促失敗・webhook 障害を検知できない | **早期対応** |
| 2 | 中 | ログに氏名・社員番号が平文で出力される | 将来対応 |
| 3 | 中 | CloudTrail のデータイベント未設定。提出物へのアクセス監査ができない（[BS_INF-07](BS_INF-07_IAM設計書.md) §5） | 将来対応 |
| 4 | 低 | `logRetention` が CDK deprecated API。v3 移行時に書き換えが必要 | 将来対応 |
| 5 | 低 | 営業のみ 14日で他と不揃い | 対応不要（業務性質が異なる） |

---

## 7. 関連文書

- Lambda 一覧 … [BS_INF-02](BS_INF-02_Lambda設計書.md)
- 定期実行と失敗検知 … [BS_INF-06 EventBridge設計書](BS_INF-06_EventBridge設計書.md) §5
- 監査ログ … [BS_INF-07 IAM設計書](BS_INF-07_IAM設計書.md) §5
