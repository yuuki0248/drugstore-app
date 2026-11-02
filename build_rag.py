"""
1. jsmi_drugs.csv を otc_drugs.db の jsmi_drugs テーブルにインポート
2. drugs + jsmi_drugs の「症状・効能」「有効成分」を sqlite-vec でベクトル化
   - ベクトルテーブル: drugs_vec
   - インデックステーブル: drug_index（rowid 対応表）
"""

import csv
import sqlite3
import sys
import sqlite_vec
from sentence_transformers import SentenceTransformer

DB_PATH = "C:/Users/acey-/dev/projects/drugstore-app/otc_drugs.db"
CSV_PATH = "C:/Users/acey-/dev/projects/drugstore-app/jsmi_drugs.csv"
MODEL_NAME = "paraphrase-multilingual-mpnet-base-v2"
EMBED_DIM = 768
BATCH_SIZE = 64

# ── 1. jsmi_drugs テーブルに CSV をインポート ──────────────────────────────

def import_jsmi_drugs(conn):
    print("=== Step 1: jsmi_drugs テーブルのインポート ===")

    conn.execute("DROP TABLE IF EXISTS jsmi_drugs")
    conn.execute("""
        CREATE TABLE jsmi_drugs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            製品名      TEXT NOT NULL,
            メーカー名  TEXT,
            薬効分類    TEXT,
            剤形        TEXT,
            医薬品分類  TEXT,
            症状効能    TEXT,
            有効成分    TEXT,
            JANコード   TEXT
        )
    """)

    excluded = 0
    inserted = 0
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows_to_insert = []
        for row in reader:
            cat = row.get("医薬品分類", "").strip()
            if not cat or cat == "第一類医薬品":
                excluded += 1
                continue
            rows_to_insert.append((
                row.get("製品名", "").strip(),
                row.get("メーカー名", "").strip(),
                row.get("薬効分類", "").strip(),
                row.get("剤形", "").strip(),
                cat,
                row.get("症状・効能", "").strip(),
                row.get("有効成分", "").strip(),
                row.get("JANコード", "").strip(),
            ))

    conn.executemany(
        "INSERT INTO jsmi_drugs (製品名,メーカー名,薬効分類,剤形,医薬品分類,症状効能,有効成分,JANコード) VALUES (?,?,?,?,?,?,?,?)",
        rows_to_insert
    )
    inserted = len(rows_to_insert)
    conn.commit()
    print(f"  除外: {excluded} 件（第一類・分類空）")
    print(f"  登録: {inserted} 件")
    return inserted


# ── 2. RAG ベクトルテーブル構築 ─────────────────────────────────────────────

def build_vec_tables(conn, model):
    print("\n=== Step 2: drug_index + drugs_vec テーブル構築 ===")

    # インデックステーブル（rowid の対応表）
    conn.execute("DROP TABLE IF EXISTS drug_index")
    conn.execute("""
        CREATE TABLE drug_index (
            id        INTEGER PRIMARY KEY,
            source    TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            text      TEXT
        )
    """)

    # vec0 仮想テーブル
    conn.execute("DROP TABLE IF EXISTS drugs_vec")
    conn.execute(f"""
        CREATE VIRTUAL TABLE drugs_vec USING vec0(
            embedding float[{EMBED_DIM}]
        )
    """)

    # drugs テーブルから取得（列名は bytes で返ることがあるため decode 対応）
    print("  drugs テーブルを取得中...")
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace") if isinstance(b, bytes) else b
    rows_drugs = conn.execute(
        "SELECT id, 効能効果, 成分 FROM drugs"
    ).fetchall()

    # jsmi_drugs テーブルから取得
    print("  jsmi_drugs テーブルを取得中...")
    rows_jsmi = conn.execute(
        "SELECT id, 症状効能, 有効成分 FROM jsmi_drugs"
    ).fetchall()

    # drug_index に挿入するデータを構築
    index_rows = []
    texts = []

    for drug_id, efficacy, ingredient in rows_drugs:
        text = " ".join(filter(None, [efficacy or "", ingredient or ""])).strip()
        index_rows.append(("drugs", drug_id, text))
        texts.append(text if text else "（情報なし）")

    for drug_id, symptom, ingredient in rows_jsmi:
        text = " ".join(filter(None, [symptom or "", ingredient or ""])).strip()
        index_rows.append(("jsmi_drugs", drug_id, text))
        texts.append(text if text else "（情報なし）")

    total = len(texts)
    print(f"  合計 {total} 件のテキストをベクトル化します...")

    # drug_index に一括挿入（rowid = 1-based）
    conn.executemany(
        "INSERT INTO drug_index (id, source, source_id, text) VALUES (?, ?, ?, ?)",
        [(i + 1, src, sid, txt) for i, (src, sid, txt) in enumerate(index_rows)]
    )
    conn.commit()

    # バッチ処理でエンベディング生成・挿入
    for start in range(0, total, BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        embeddings = model.encode(batch, show_progress_bar=False, normalize_embeddings=True)
        rows_vec = [
            (start + i + 1, emb.tobytes())
            for i, emb in enumerate(embeddings)
        ]
        conn.executemany(
            "INSERT INTO drugs_vec (rowid, embedding) VALUES (?, ?)",
            rows_vec
        )
        conn.commit()
        done = min(start + BATCH_SIZE, total)
        print(f"  [{done}/{total}] 完了", end="\r", flush=True)

    print(f"\n  ベクトル登録完了: {total} 件")


# ── 3. 動作確認 ──────────────────────────────────────────────────────────────

def verify(conn, model):
    print("\n=== Step 3: 動作確認 ===")

    # 件数確認
    drugs_count = conn.execute("SELECT COUNT(*) FROM drugs").fetchone()[0]
    jsmi_count  = conn.execute("SELECT COUNT(*) FROM jsmi_drugs").fetchone()[0]
    index_count = conn.execute("SELECT COUNT(*) FROM drug_index").fetchone()[0]
    vec_count   = conn.execute("SELECT COUNT(*) FROM drugs_vec").fetchone()[0]

    print(f"  drugs:      {drugs_count} 件")
    print(f"  jsmi_drugs: {jsmi_count} 件")
    print(f"  drug_index: {index_count} 件")
    print(f"  drugs_vec:  {vec_count} 件")

    # 類似検索テスト
    query = "頭痛 解熱"
    print(f"\n  検索テスト: 「{query}」")
    q_vec = model.encode([query], normalize_embeddings=True)[0].tobytes()

    results = conn.execute("""
        SELECT di.source, di.source_id, di.text, dv.distance
        FROM drugs_vec dv
        JOIN drug_index di ON di.id = dv.rowid
        WHERE dv.embedding MATCH ?
          AND K = 5
        ORDER BY dv.distance
    """, (q_vec,)).fetchall()

    for source, sid, text, dist in results:
        snippet = text[:60].replace("\n", " ")
        print(f"    [{source} id={sid}] dist={dist:.4f} | {snippet}...")

    print("\n完了。")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)

    import_jsmi_drugs(conn)

    print(f"\nモデル読み込み中: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    build_vec_tables(conn, model)
    verify(conn, model)

    conn.close()


if __name__ == "__main__":
    main()
