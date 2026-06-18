# 要員通勤コスト調査 原型（プロトタイプ）

出発駅・到着駅を与えると、**通勤時間／乗換回数／片道運賃（IC・きっぷ）／通勤定期代（1・3・6ヶ月）**
を取得する個人検証用ツール。社内提案時に「各要員 → 客先現場」の通勤コストを横並び比較する用途。

> ⚠️ **データソースは暫定です。** 現状は ekitan（駅探）の Web ページを解析していますが、
> これは **原型段階の一時的なデータソース**です。正式版は授権 API（**駅すぱあと / 駅探法人**）に
> 差し替えます。そのため業務ロジックは ekitan の HTML 構造に直接依存せず、
> 差し替え可能な抽象層（`TransitDataSource`）越しにのみデータを取ります（後述）。

---

## セットアップ

```bash
pip install -r requirements.txt
```

依存は `requests` と `beautifulsoup4` のみ（理由は「playwright を使わない理由」参照）。

## 使い方

```bash
# 駅名で（stations.json から ID を引く）
python commute.py 平井 東京

# 駅IDを直接（2927 / sf-2927 / st-2590 いずれでも可）
python commute.py 2927 2590

# 戦略を切り替え（既定は cheapest）
python commute.py 平井 東京 --strategy fastest

# JSON 出力
python commute.py 平井 東京 --json

# キャッシュを無視して取り直す
python commute.py 平井 東京 --no-cache
```

### 「平井 → 東京」の実行例

```
$ python commute.py 平井 東京
平井(東京) → 東京 | 18分 / 乗換1回 | IC片道 209円 / きっぷ 210円 | 定期 1ヶ月 6,240円 (3ヶ月 17,780 / 6ヶ月 33,700) | 8.2km | 経由: 平井(東京)→錦糸町→東京 | [cheapest/ekitan]
```

`--json` の出力（1 経路ぶんの構造化オブジェクト）:

```json
{
  "from_station": "平井(東京)",
  "to_station": "東京",
  "duration_min": 18,
  "transfers": 1,
  "fare_ic_yen": 209,
  "fare_ticket_yen": 210,
  "pass_1month_yen": 6240,
  "pass_3month_yen": 17780,
  "pass_6month_yen": 33700,
  "route_summary": "平井(東京)→錦糸町→東京",
  "distance_km": 8.2,
  "strategy": "cheapest",
  "source": "ekitan",
  "queried_at": "2026-06-17T15:10:00",
  "from_cache": false
}
```

> 候補が複数あるため、選び方（戦略）次第で結果は変わります。上の例では運賃 209/210 円は
> どの候補も同じですが、定期代の安い経路を選んでいます。実際の数値は取得時点のダイヤで変動します。

---

## データソースの切り替え（DATA_SOURCE）

業務層は `TransitDataSource` 抽象だけに依存し、実装は環境変数で選びます。

| DATA_SOURCE | 実装 | 説明 |
|-------------|------|------|
| `ekitan_scraper`（既定） | EkitanScraper | ekitan の Web ページを解析 |
| `ekispert_api` | EkispertApiSource | 駅すぱあと API を呼ぶ |

```bash
# 既定（ekitan）
python commute.py 平井 東京

# ekispert に切替（環境変数 or --data-source）
export EKISPERT_API_KEY=xxxxx            # ハードコード禁止・必ず環境変数
DATA_SOURCE=ekispert_api python commute.py 平井 東京
python commute.py 平井 東京 --data-source ekispert_api
```

### ekispert は「ベストエフォート」設計

無料枠では構造化の運賃/定期代が返らず URL だけのことがあります。本実装は
**取れた項目だけ使い、取れない項目は `None` にしてログを出す（クラッシュしない）**。
所要時間だけでも経路選択できるよう、戦略選択も欠損に寛容です。まず両ソースとも
動かし、構造化データが取れると確認できてから API を主にする想定です。
（定期種別は「通勤」のみ採用。通学・オフピークは除外。）

### ekispert で 403 が出るときの切り分け

1. **key の有効化**：申請直後は反映待ちのことがある。
2. **ドメイン/IP バインド**：無料枠は登録ドメイン以外（localhost / 別IP）からの
   呼び出しを弾く設定がある。駅すぱあとの管理画面でアクセス元設定を確認。
3. **HTTPS 必須**：`https://api.ekispert.jp/...` を使う（本実装は HTTPS 固定）。
4. それでも 403 なら当面 `DATA_SOURCE=ekitan_scraper` で運用（フォールバック）。

---

## 経路選択の戦略

ページには候補経路が複数返るため、**先頭を盲目的に採らず**明示ルールで 1 件に絞ります。

| 戦略 | ルール |
|------|--------|
| `cheapest`（既定） | **通勤定期代（1ヶ月）が最安**。同額なら所要時間が短い方。 |
| `fastest` | **所要時間が最短**。同時間なら定期代が安い方。 |

`--strategy {cheapest,fastest}` で切り替え。戦略はキャッシュ済み候補に対してその場で
適用されるので、戦略を変えても再リクエストは発生しません。

---

## 駅マスタ（stations.json）

ekitan の URL は駅名ではなく**内部数字ID**（例: 平井=`2927`）を食います。そこで
「駅名 → 駅ID」対照表を `stations.json` に持ち、入力の入口にしています。

```json
{
  "平井": "2927",
  "東京": "2590"
}
```

値は 2 形式を許容します（同梱の master は前者の文字列形式・ekitan ID）:

```json
{
  "東京": "2590",
  "横浜": { "ekitan": "3260", "ekispert": "22828" }
}
```

- 文字列 `"2590"` … ekitan の駅IDとして扱う（後方互換）。
- オブジェクト … データソース別のコードを保持。`ekispert` コードが無い駅は、
  ekispert 実装が駅名から `station/light` API で解決します（未整備でも動く）。

### 新しい駅IDの拾い方

ekitan の駅IDは `sf-`（出発 = start from）/ `st-`（到着 = station to）の後ろの**数字**です。
同じ駅なら出発でも到着でも数字は同じ（例: 東京は `sf-2590` でも `st-2590` でも `2590`）。
`stations.json` には接頭辞を付けず**数字だけ**入れます。拾い方は3通り。

#### 方法A: ekitan のページ URL から読む（基本・確実）

1. ekitan（https://ekitan.com）でその駅を検索し、**乗換 or 運賃ページ**を開く。
2. URL の `sf-XXXX` / `st-XXXX` の **数字部分**がその駅のID。
   例: `https://ekitan.com/transit/fare/sf-2927/st-2590`
   → 出発駅(平井)=`2927`、到着駅(東京)=`2590`
3. `stations.json` に `"駅名": "数字ID"` を追記。

#### 方法B′: 駅サジェスト API で引く（一番おすすめ・付属ツールあり）⭐

ekitan の検索ボックスが使う内部 API がそのまま「駅名 → 駅ID」を返します（lkey 不要）:

```
GET https://mob-gw.ekitan.com/inc/v2/n_station?q=<駅名>&c=
-> [{"result":[{"code":"1541","name":"池袋","ruby":"いけぶくろ","area":"0","company":"..."}]}]
```

- `code` がそのまま ekitan 駅ID。`area=0` が**首都圏（=東京周辺）**。
- 部分一致なので、`name` 完全一致 + `area=0` で絞ると確実。

付属ツール `resolve_station.py` がこれをラップしています（誤ID登録の事故防止用）:

```bash
python resolve_station.py 池袋 横浜 川口        # 候補表示（首都圏を優先）
python resolve_station.py 平井 --all            # 全国の同名（平井(東京)/平井(愛媛)）も表示
python resolve_station.py 池袋 横浜 --add        # 首都圏で一意なら stations.json に自動追記
```

> 内部 API なので **少量・直列・低頻度**で。正式版は駅すぱあと/駅探法人の公式駅マスタに置換予定。

#### 方法C′: 首都圏を一括収集する（付属ツール `harvest_tokyo_stations.py`）

ekitan の時刻表ページは公開・robots許可で、エリア→路線→駅の階層をたどれます:

```
/timetable/railway/area/0          首都圏の路線一覧（約160路線）
/timetable/railway/line/<lineId>   各路線の全駅（/station/<駅ID> リンク＋駅名）
```

これを順にたどって**首都圏 約2,384駅**を一括収集し、stations.json に書き出します:

```bash
python harvest_tokyo_stations.py            # 全路線を収集（約160リクエスト・直列・2秒間隔で5〜6分）
python harvest_tokyo_stations.py --limit 3  # 動作確認用
```

> 同梱の `stations.json` はこのツールで生成済み（首都圏路線の全駅）。路線は県境を越えるため
> 宇都宮・高崎・箱根などエリア外の終端駅も一部含みます（通勤元になり得るので許容）。
> サジェストAPIの10件上限を避けつつ全量を取れる、最も実用的な一括手段です。

#### 方法B: 検索エンジンで逆引き（速い）

`ekitan <駅名> 運賃` などで検索すると、ヒットした ekitan の URL に `sf-`/`st-` 付きの
IDが入っています。実際この方法で 池袋=`1541`、横浜=`3260` を確定しました。
例: `https://ekitan.com/transit/fare/sf-1541/st-2590` → 池袋=`1541`。
※ヒットURLの **駅名と数字の対応**を必ず目視確認してから採用すること（方法A同様）。

#### 方法C: 経路JSONの中転駅コードから拾う（まとめ取り向き）

運賃ページの各経路 `<div data-ek-route-json='{...}'>` には、経由する各駅の `code`
（＝そのままの駅ID）が入っています。**既知の駅どうしで、目的の駅を通る経路**を1回引けば、
その駅のIDを中転駅として回収できます（例: 知っている2駅間の経路に新宿が出てくれば
新宿のコードが取れる）。コードは各セグメントの
`lineList.line[].stationFrom.A.code` / `stationTo.A.code`、駅名は同じ階層の
`stationName` にあります。新規駅をまとめて集めたいときに便利。

**よく使う客先現場の駅を手で集めて少しずつ貯めていく**運用を想定しています。
`stations.json` には `sf-`/`st-` を付けず数字だけ入れてください（接頭辞は ekitan の URL 都合で、
コード側が内部で付けます。正式版 API に替えても対照表の形は変わりません）。

> 駅名でも `2927` でも `sf-2927` でも渡せます（コードが数字IDに正規化します）。

> ⚠️ **IDの取り違いに注意**：誤ったIDでも別の実在駅として黙って通ってしまいます
> （例: `池袋=2593` は実際には *塔ノ沢*、`横浜=1525` は *淡路町*）。`stations.json` に
> 追加したら、まず一度実行して **出力の `from_station` / `to_station` が意図した駅名か**を
> 確認してください。正しい既知の値: 平井=`2927`, 東京=`2590`, 池袋=`1541`, 横浜=`3260`。

---

## キャッシュ

- 同じ `(出発ID, 到着ID)` を一度引いたら、解析済みの候補リストを `./.cache/` に JSON 保存します。
- 既定では **TTL 30 日**以内ならキャッシュを返し、**一切リクエストを出しません**（このツールが
  リクエストを節約する肝）。
- TTL は `--ttl-days`（負値で無期限）、無効化は `--no-cache` で。
- キャッシュキーは出発/到着のみ。**戦略を変えても再取得しません**。

`./.cache/` には取得した `robots.txt`（後述）と経路 JSON が入ります。`.gitignore` 済み。

---

## 責任あるクロール（robots.txt）

このツールは **十数人規模の内部ツール**で、総リクエスト量を極小に保つ設計です。

- 実在ブラウザ相当の **User-Agent** を設定（素性を明示）。
- **リクエスト間に必ずスリープ**（既定 3 秒、`--delay` で調整、2 秒以上推奨）。直列のみ・並列にしない。
- **ディスクキャッシュ**で同一クエリの再取得を回避（上述）。
- 起動時に **robots.txt を取得・キャッシュ（7 日）**し、`urllib.robotparser` で取得可否を判定。

### ekitan の robots.txt（2026-06-17 確認）

運賃/定期に関する `Disallow` は**すべてクエリ文字列付き** (`?*`) が対象です:

```
Disallow: /transit/fare?*
Disallow: /transit/fare/*/*?*
Disallow: /transit/pass?*
Disallow: /transit/pass/*/*?*
```

本ツールが使う **クエリ無しの正準 URL** は許可されています:

```
/transit/fare/sf-{from}/st-{to}      ← 使用（許可）
/transit/pass/sf-{from}/st-{to}      ← 使用（許可）
```

そのため**コードは URL にクエリ文字列を一切付けません**（`transit/robots.py` の `assert` で担保）。
その他 `/m/`（モバイル）, `/click/`, `/transit/*/ajax/*` 等は Disallow なので使いません。
`Crawl-delay` 指定は無し（こちらで 3 秒スリープを課しています）。
`User-agent: ClaudeBot` 向けには `/d-norikae/` `/d-timetable/` のみ Disallow で、本ツールの対象外。

---

## アーキテクチャ（差し替え契約）

2 層に分けています。

```
commute.py（上位層 / CLI）
   │  依存するのは ↓ の抽象だけ
   ▼
TransitDataSource（抽象基底） ──実装──▶ EkitanScraper   （今：ekitan HTML 解析）
   │  query(from_id, to_id, strategy) -> CommuteResult
   │                            └──実装──▶ EkispertApiSource（将来：駅すぱあと API）※未実装
StationRegistry（stations.json: 駅名 ⇄ ID）
```

- **上位層は `TransitDataSource` インタフェースと `CommuteResult` にしか依存しません。**
- `EkitanScraper` には ekitan 固有の知識（HTML/JSON 構造・候補リストの並び・`sf-`/`st-` 等）を
  **すべて閉じ込め**、インタフェースには漏らしていません。
- `from_id`/`to_id` は「そのデータソースが理解する駅ID」を表す不透明な文字列です。

### 正式版への差し替え手順（駅すぱあと等）

1. `TransitDataSource` を継承した `EkispertApiSource` を作る。
2. `query(from_id, to_id, strategy)` を実装し、**同じ `CommuteResult` を返す**。
3. `commute.py` の `build_source()` の 1 行を差し替える。

→ **`main` と駅マスタ層は 1 行も変えなくて済みます。** `stations.json` は新ソースの駅ID体系で
維持し直すだけ（構造は同じ）。

### ファイル構成

```
commute.py              CLI エントリ（単一クエリ・上位層）
query.py                現場→全要員 一括比較 CLI（Phase 3/4）
staff_admin.py          要員DB 管理 CLI（Phase 2）
resolve_station.py      駅名→駅ID 検索ツール（個別調査・stations.json 整備用）
harvest_tokyo_stations.py  首都圏 全駅ID 一括収集ツール（時刻表ページから）
stations.json           駅マスタ（駅名 → 駅ID。首都圏 約2,384駅を収録）
staff/
  models.py             Staff / StaffStatus（保存層非依存）
  repository.py         StaffRepository 抽象（= 保存の差し替え契約）
  sqlite_repository.py  SQLiteStaffRepository（ゼロ依存）
  seed.py               サンプル要員（BrightStar roster 準拠）
requirements.txt
transit/
  batch.py              一括比較（compare_site / CSV・端末出力。同駅は1回だけ問い合わせ）
  models.py             CommuteResult / Strategy（データソース非依存の戻り値）
  exceptions.py         共通例外（解析失敗時は探した項目＋生断片を添える）
  data_source.py        TransitDataSource 抽象基底（= 差し替え契約）
  config.py             DATA_SOURCE 切替 + build_source ファクトリ
  registry.py           StationRegistry（駅名 ⇄ ソース別ID 正規化）
  station_lookup.py     駅サジェスト API ラッパ（駅名→駅ID 解決）
  cache.py              DiskCache（TTL 付きディスクキャッシュ）
  robots.py             RobotsGate（robots.txt 尊重）
  ekitan_source.py      EkitanScraper（ekitan 固有の解析はここだけ）
  ekispert_source.py    EkispertApiSource（駅すぱあと API。無料枠フォールバック対応）
```

---

## 要員DB（Phase 2）

「現場 → 全要員の通勤コスト一括比較」のための要員データ層。SQLite（標準ライブラリ・ゼロ依存）。

### データモデルと個人情報配慮

| 項目 | 内容 | 由来 |
|------|------|------|
| `staff_id` | 要員ID = **BrightStar 花名册(roster) の empId**（E002…） | BrightStar |
| `name` / `department` | 氏名・部署 | BrightStar roster 由来 |
| `nearest_station` | **最寄駅のみ**（駅名 or ID） | このツール固有 |
| `status` | `available`(待機) / `assigned`(現場確定) | このツール固有 |
| `assigned_site` / `updated_at` | 確定現場 / 更新時刻 | このツール固有 |

- **住所は持たず最寄駅だけ**を保存：提案のコスト試算にはこれで十分で、個人情報の
  機微度を下げられる（漏れても影響が小さい）。
- **`assigned` マークの意図**：現場確定済みの要員は通常もう動かさないので、一括比較
  （Phase 3）は既定で `available`（待機中＝次の現場を探している人）だけを対象にする。
  月末の異動で `bulk_upsert` / `update_status` でまとめて更新する。

### 使い方（CLI / 関数）

```bash
python staff_admin.py seed                       # サンプル要員投入（BrightStar roster準拠 E002〜E005）
python staff_admin.py list --status available    # 待機中だけ
python staff_admin.py add E006 田中次郎 川口 --dept 営業部
python staff_admin.py status E002 assigned --site 東京   # 現場確定
python staff_admin.py station E003 品川                   # 最寄駅変更
python staff_admin.py delete E006
```

関数として使う場合は `staff.SQLiteStaffRepository`（`upsert/get/list/update_status/
set_nearest_station/delete/bulk_upsert`）。

### BrightStar 社内システムとの DB 統合

BrightStar（勤怠・通勤費 LINE Bot）は花名册 **roster**（DynamoDB, PK=`empId`、
name/department/role）を「人員の主データ」として持つ。本ツールは:

- 要員の主キー `staff_id` を roster の `empId` に揃え、`name`/`department` も roster 表記に合わせる
  → **同じキーで結合できる**。サンプルも BrightStar の demo roster と一致させてある。
- 保存層は `StaffRepository` 抽象で隠蔽。今は `SQLiteStaffRepository`、将来は
  roster を読む `BrightStarRosterRepository`（DynamoDB から who/name/dept を取得し、
  通勤固有項目 nearest_station/status は別テーブルに保持）に**差し替えるだけ**。業務層は無改修。

> 統合の具体方式（①スキーマ整合のローカルミラー＝今、②roster を直読みする実装、
> ③定期同期ジョブ）は実際の運用に合わせて選びます。**実DynamoDB直結は Phase 3 で要相談**
> （AWS 認証情報やネットワークが絡むため）。Phase 2 は単体起動するミラー実装まで。

---

## 一括比較（Phase 3 / `query.py`）

現場（到着駅）への、全要員の通勤コストを一括比較する。

```bash
python query.py --site 東京                  # 待機中(available)の全要員
python query.py --site 新宿 --strategy fastest
python query.py --site 東京 --status all     # assigned 含む全員
python query.py --site 東京 --csv out.csv     # CSV(Excel可)も出力
```

- 既定で **status=available（待機中）だけ**を対象（assigned は現場確定済みなので除外）。
- **同じ最寄駅は 1 回しか問い合わせない**：実行内メモ＋データソースのディスクキャッシュの
  二段で、要員が同じ駅を共有していても重複リクエストしない（請求節約の肝）。
- 戦略順（cheapest=定期1ヶ月昇順 / fastest=所要時間昇順）に並べ、合計・平均も出す。失敗者は末尾。

---

## AWS デプロイ（CDK）

Serverless 構成（東京 ap-northeast-1）でデプロイ済み。ローカルと同じ業務ロジックを、
キャッシュ=DynamoDB・要員DB=DynamoDB に差し替えて Lambda で動かす（差し替え契約のおかげで再実装不要）。

```
[Function URL (AWS_IAM)] → [Lambda Python3.12  handler.handler]
                                 ├─ transit/ (ekitan スクレイパ) + 依存レイヤ(requests/bs4)
                                 ├─ staff/   (要員)
                                 ├─ DynamoDB: staff（PK staff_id=empId）
                                 └─ DynamoDB: cache（PK cacheKey, 30日TTLで自動失効）
```

### 構成のポイント
- **Function URL は `AWS_IAM` 認証**：公開すると外部から無制限にスクレイピングを誘発でき、
  ekitan への責任あるアクセスに反するため。呼び出しは SigV4 署名が必要（`call_api.py`）。
- **依存レイヤ**：`requests`/`bs4` は Lambda 標準に無いので Layer 化（`infra/build.py` が
  `--platform manylinux2014_x86_64` で Linux wheel を取得。Docker 不要）。
- **責任あるクロール**：`REQUEST_DELAY_SEC=3` で Lambda 内でも sleep。DynamoDB キャッシュ
  （30日TTL）＋同一駅の実行内メモで再スクレイピングを抑制。
- DynamoDB は従量（オンデマンド）課金。要員DBは BrightStar roster と同じ `empId` をキーに（独立テーブル）。

### デプロイ手順

```bash
cd infra
npm install
python build.py                                   # Lambda資産 + 依存レイヤを作成
export AWS_REGION=ap-northeast-1                   # PowerShell: $env:AWS_REGION="ap-northeast-1"
npx cdk deploy EkiCommute-dev --require-approval never
```

### 呼び出し（SigV4 署名）

`call_api.py` が default プロファイルで署名して叩く（`--url` は deploy 出力の FunctionUrl）:

```powershell
$env:COMMUTE_API_URL="https://xxxx.lambda-url.ap-northeast-1.on.aws/"
python call_api.py POST "/seed"                       # サンプル要員投入
python call_api.py GET  "/staff?status=available"
python call_api.py GET  "/query?from=池袋&to=東京"
python call_api.py GET  "/site?site=東京&strategy=cheapest"   # 現場→全要員 一括比較
python call_api.py GET  "/site?site=東京&format=csv"          # CSV
```

> ⚠️ git-bash だと `/seed` 等の先頭 `/` が Windows パスに書き換えられて失敗する。
> 呼び出しは **PowerShell** で行うこと（CLAUDE.md の MSYS 書き換え坑）。

### 後片付け（課金停止）

```bash
cd infra && AWS_REGION=ap-northeast-1 npx cdk destroy EkiCommute-dev
```

DynamoDB は `RemovalPolicy.DESTROY` なのでスタック削除で消える（本番運用するなら RETAIN を検討）。

---

## playwright を使わない理由

要件どおり、まず requests で取れるか検証しました。運賃・定期ページは**サーバサイド
レンダリング**で、必要なデータはすべて初期 HTML に含まれています:

- 運賃ページ: 各候補 `<div class="ek-route" data-ek-route-json='{...}'>` に
  所要時間・乗換・走行距離・経由駅・路線が **JSON で**入っている（＝ページ自身のデータモデル。
  脆い DOM パスに頼らず、この JSON とアンカークラスを使って堅く解析しています）。
  運賃は `span.ek-ticket_total`（きっぷ片道）/ `span.ek-ic_total`（IC片道）。
- 定期ページ: `data-ek-display="business"` のブロックに通勤 1/3/6ヶ月が入る
  （通学=college 等、オフピーク=businessOffPeak は除外。完全一致で business のみ採取）。

したがって **playwright は不要**です。将来ページが JS 描画に変わって HTML から取れなくなった
場合に限り、playwright 等への退避を検討してください（その場合も解析は `EkitanScraper` 内に閉じます）。

---

## 解析の堅牢性メモ

- 金額の千分位カンマ（`6,240円`）は数字以外を除去して `int` 化。
- 想定フィールドが見つからない場合は **どの項目を探していたか＋拾えた生 HTML 断片**を添えた
  `ParseError` を投げます（ページ改版時の一次切り分け用）。
- 運賃ページと定期ページは別クエリのため、**路線シグネチャ（路線名の並びを正規化したもの）**を
  キーに突き合わせて 1 経路に統合します。徒歩区間は除外し、運賃ページで同一路線が複数区間に
  割れている場合は畳んで定期ページ側と一致させます。
  - **所要時間はキーに使いません**。運賃ページ（乗車時間）と定期ページ（待ち時間込みの総時間）で
    値が一致せず、長距離・乗換の多い経路ほど大きくずれるためです。同一シグネチャの経路が複数ある
    ときは、両ページとも rank 順に並ぶ前提で出現順に対応付けます。
  - 万一シグネチャが 1 件も一致しない場合は rank 順インデックスで代替対応し、警告を出します。
```
