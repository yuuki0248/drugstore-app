# drugstore-app — Claude Code ガイド

## プロジェクト概要

PMDA（医薬品医療機器総合機構）の OTC 市販薬データベースを使った薬局向け支援ツール。
お客様の情報をもとに DB を検索し、Claude が禁忌チェック・提案を行うことを最終目標とする。

---

## 構成ファイル

| ファイル | 役割 |
|---|---|
| `otc_drugs.db` | SQLite DB（7,243件、第1類・リスク区分空を除外済み） |
| `api/search.js` | Vercel サーバーレス API（sql.js で SQLite 検索、第2・3類のみ返す） |
| `otc_mcp_server.py` | Claude Code に登録済みの MCP サーバー（症状・成分・リスク区分で検索） |
| `pmda_otc_scraper.py` | PMDA スクレイパー（再取得が必要な場合のみ使用） |
| `index.html` | フロントエンド（未実装：api/search.js との接続が次のタスク） |

---

## DB スキーマ（`drugs` テーブル）

```
id, 販売名, 製造販売会社, 詳細URL, リスク区分, 効能効果, 成分
```

- リスク区分の値：`第２類医薬品` / `第「２」類医薬品` / `第３類医薬品`

---

## API（`api/search.js`）

- **エンドポイント**: `GET /api/search?keyword=頭痛&risk=第２類医薬品`
- `keyword` 必須。`risk` は省略可（省略時は第2・3類すべて対象）
- 販売名 → 効能効果 → 成分 の優先順位で最大 20 件返す
- `sql.js`（WASM）使用。`better-sqlite3` は Node 24 でビルド不可のため不採用

---

## MCP サーバー（`otc_mcp_server.py`）

Claude Code に登録済み（名前: `otc-drugs`）。以下の 3 ツールを提供：

- `search_by_symptom(keyword, risk_category?)` — 効能効果で検索
- `search_by_ingredient(ingredient_name, risk_category?)` — 成分名で検索
- `filter_by_risk_category(risk_category, keyword?, limit?)` — リスク区分で絞り込み

uv の絶対パスで登録済み: `C:\Users\acey-\.local\bin\uv.exe run --python 3.12 ...`

---

## デプロイ

- **GitHub**: `https://github.com/yuuki0248/drugstore-app`
- **Vercel**: デプロイ済み（`api/search.js` が稼働中）

---

## 今後のタスク

1. **`index.html` と `api/search.js` を接続する**
   - 流れ: お客様情報入力 → DB 検索（`/api/search`） → Claude 禁忌チェック → 薬の提案
   - `index.html` に検索フォームと結果表示を実装する

2. **Ollama（ローカル LLM）は保留**
   - 現在のスペック: RAM 8GB・GPU なし（Intel HD Graphics 530 内蔵のみ）
   - llama3.2 をインストール済みだが実用速度が出ないため保留
   - RAM 16GB 以上・専用 GPU 搭載 PC に買い替えたら再挑戦

---

## 注意事項

- `otc_drugs.db`（4MB）は git 管理対象。`.gitignore` に除外設定なし
- CSV ファイル（`*.csv`）は `.gitignore` で除外済み
- スクレイパーを再実行する場合は PMDA への負荷を考慮して 3 並列・1 秒 sleep を維持すること
