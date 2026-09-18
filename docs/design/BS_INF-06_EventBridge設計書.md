# BrightStar 統合LINEアシスタント EventBridge設計書

| 項目 | 内容 |
|---|---|
| 文書番号 | BS_INF-06 |
| 文書名 | EventBridge設計書 |
| 対象システム | BrightStar 統合LINEアシスタント（5チャネル） |
| 版数 | 初版 |
| 最終更新 | 2026-09-19 |
| 実機照合 | 2026-09-19 |

---

## 1. 方針

| 項目 | 設計 | 理由 |
|---|---|---|
| スケジューラ | **EventBridge ルール（cron / rate）** | Lambda を時刻起動する最も単純な方法。追加のインフラを持たない |
| EventBridge Scheduler | 使わない | 1回限りの予約実行には Scheduler が適するが、**予約1件ごとに AWS リソースが増える**運用を避けたい。本システムは予約を DynamoDB に持ち、**固定間隔のポーラー**が拾う方式にした（§3） |
| イベントバス | 既定バス（`default`）のみ | カスタムバス・イベント連携は不要 |
| タイムゾーン | cron は **UTC 指定**。JST 換算を式に織り込む | EventBridge の cron は UTC 固定。Lambda 側は環境変数 `TZ=Asia/Tokyo` で JST として動作する |

---

## 2. ルール一覧（2026-09-19 実測）

| ルール名 | スケジュール（UTC） | JST | 起動先 | 用途 |
|---|---|---|---|---|
| `brightstar-soumu-dev-reminder-schedule` | `cron(0 0 25,28 * ? *)` | 毎月 25日・28日 **9:00** | `brightstar-soumu-dev-reminder` | 未提出者への自動催促 |
| `brightstar-soumu-dev-booking-poller` | `rate(10 minutes)` | 10分ごと | `brightstar-soumu-dev-reminder` | 催促予約の期限到来分を実行 |
| `brightstar-hr-dev-reconcile-schedule` | `cron(0 15 * * ? *)` | 毎日 **0:00** | `brightstar-hr-dev-reconcile` | LINE 紐付けの到達性点検 |
| `brightstar-kenshu-dev-ReminderTick…` | `rate(10 minutes)` | 10分ごと | `brightstar-kenshu-dev-ReminderFunction` | 研修の開講1時間前リマインド |

すべて `ENABLED`。

### スケジュール値の根拠

| ルール | 根拠 |
|---|---|
| 25日・28日 9:00 | 月末締めの書類に対し、**締切前に 2 回**の猶予を作る。9:00 は始業直後で気付きやすい。UTC `0 0` が JST 9:00 に相当 |
| 毎日 0:00（UTC 15:00） | 日付が変わるタイミング＝認証テーブルの TTL 失効と同期。退職・ブロックされたアカウントを翌営業日までに掃除する |
| 10分間隔 | 予約催促・研修リマインドの**許容遅延**。「9:00 に送る」予約が最大 9 分遅れて 9:09 になっても業務上問題ない。1分間隔にすると呼び出し回数が 10 倍になり、得るものが無い |

---

## 3. 予約催促の実行方式（ポーラー方式）

```
総務が「催促予約 2026-07-25 09:00」
  → bookings テーブルに { bookingId, runAtEpoch, status: pending } を登録
                ↓
10分ごとの booking-poller → reminder Lambda を trigger:"poll" で起動
  → runAtEpoch <= now かつ status=pending を抽出して実行
  → 実行時点の未提出者のみに送信 → status=sent
```

| 項目 | 設計 | 理由 |
|---|---|---|
| 予約の保持先 | DynamoDB `bookings` | 予約ごとに AWS リソース（Scheduler・ルール）を作らない。**予約の一覧・取消が DynamoDB の操作だけで完結**する |
| 実行判定 | ポーラーが `runAtEpoch <= now` で抽出 | |
| 送信対象 | **実行時点**で未提出の人だけ | 予約時点の未提出者に送ると、その後に提出した人にも催促が飛んでしまう |

---

## 4. Lambda 起動の経路

reminder Lambda は**3 つの経路**から起動される。イベントの `trigger` フィールドで分岐する。

| trigger | 起動元 | 処理 |
|---|---|---|
| （なし） | EventBridge `reminder-schedule` | 定時の自動催促 |
| `poll` | EventBridge `booking-poller` | 予約分の実行 |
| `broadcast` | 総務 webhook からの非同期 Invoke | 一斉送信 |
| `notify` | 総務 webhook からの非同期 Invoke | 提出物削除時の再提出依頼（単発 push） |

> webhook から reminder を呼ぶのは **`InvocationType=Event`（非同期）**。LINE の 30 秒制限内に ack を返すため、送信処理の完了を待たない（[BS_INF-05](BS_INF-05_公開エンドポイント設計書.md) §5）。

---

## 5. 既知の課題

| # | 深刻度 | 区分 | 内容 | 推奨対応 | 要否 |
|---|---|---|---|---|---|
| 1 | **中** | 運用 | **失敗時の検知手段が無い**。催促が送られなくても誰も気付かない（CloudWatch アラーム 0 件） | reminder / reconcile の `Errors` メトリクスにアラームを設定し SNS 通知（[BS_INF-09](BS_INF-09_監視・ログ設計書.md) §4） | **早期対応推奨** |
| 2 | 低 | 構成 | EventBridge → Lambda の**DLQ 未設定**。Lambda 側の非同期実行失敗が捨てられる | 非同期 Invoke（broadcast / notify）に DLQ を設定すると、送信失敗の取りこぼしを回収できる | 将来対応 |
| 3 | 低 | 運用 | 祝日・年末年始を考慮しない。25日が休日でも催促が飛ぶ | 業務上の実害が小さい（社員は翌営業日に対応すればよい）ため対応しない判断も可 | 対応不要 |

---

## 6. 費用

EventBridge のルール実行は**月100万件まで無料**。本システムは 10 分間隔 × 2 ルール = 月約 8,640 回で、無料枠に十分収まる。

---

## 7. 関連文書

- 起動される Lambda … [BS_INF-02](BS_INF-02_Lambda設計書.md)
- `bookings` テーブル … [BS_INF-03 DynamoDB設計書](BS_INF-03_DynamoDB設計書.md) §2
- 監視 … [BS_INF-09](BS_INF-09_監視・ログ設計書.md)
