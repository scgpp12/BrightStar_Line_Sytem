# BrightStar LINE System（4 アシスタント統合 monorepo）

社内 LINE アシスタントを **責務分離（低結合）** した統合リポジトリ。各サブプロジェクトは
独立した CDK アプリで、`cdk deploy` で個別にデプロイする。機密は **SSM SecureString**
（または deploy 時の `-c` context）で注入し、**ソースには一切入れない**。

リージョン：ap-northeast-1（東京）／ アカウント：603319838936

## 構成（4 channel）

| ディレクトリ | アシスタント | 役割（LINE channel） | IaC | スタック |
|---|---|---|---|---|
| `kenshu/` | 研修アシスタント | **講師専用**（受講者は社員へ誘導） | CDK(Python) | `brightstar-kenshu-dev` |
| `jinji/`  | 人事アシスタント | **人事担当専用**（社員は社員へ誘導） | CDK(TS) | `BrightstarHr-dev` |
| `eigyo/`  | 営業アシスタント | 営業（現状維持・分割なし） | CDK(TS) | `EkiCommute-dev` |
| `shain/`  | 社員アシスタント | **一般社員**：研修(受講)＋人事(提出) を集約 | CDK(Python) | `brightstar-shain-dev` |

### 設計のポイント
- **データ共有**：社員 channel は研修(Students/Courses…)・人事(roster/submissions+S3) の
  テーブルを**名前参照で共有**。社員で受講→研修(講師)で確認、社員で提出→人事(HR)で確認、が成立。
- **同一 Provider** 前提：同一人物の LINE userId は全 channel で一致 → 既存バインドを再利用。
- **社員ルーター**：`shain/lambda/handler.py` が mode（研修/人事）でディスパッチ。
  研修側は `kenshu.*`、人事側は `jinji.*` として **vendoring**（`common`/`handlers` の名前衝突を
  名前空間化で回避）。I/O 層は社員 channel の凭证（SSM）に一本化。
- **微信/企業微信**：研修(kenshu)のみが従来どおり対応（LINE のゲートは LINE 経路だけに適用）。

## デプロイ
```bash
# Python CDK（kenshu / shain）
cd kenshu/cdk && pip install aws-cdk-lib constructs && npx cdk deploy brightstar-kenshu-dev --require-approval never
cd shain/cdk  && npx cdk deploy brightstar-shain-dev  --require-approval never
# TS CDK（jinji / eigyo）
cd jinji/cdk  && npm install && npx cdk deploy BrightstarHr-dev --require-approval never -c hrUserIds=<...>
cd eigyo/infra && npm install && python build.py && npx cdk deploy EkiCommute-dev --require-approval never
```

## 凭证（SSM SecureString、ソース非格納）
```
/brightstar-kenshu/dev/line/{secret,token}   # 研修 channel
/brightstar-hr/dev/line/{secret,token}       # 人事 channel
/eki-commute/dev/line/{channel-secret,channel-access-token}  # 営業 channel
/brightstar-shain/dev/line/{secret,token}    # 社員 channel
```
微信/企業微信の機密は kenshu デプロイ時に `-c weCom...=...` で注入（`kenshu/cdk/README.md` 参照）。
