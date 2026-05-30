import { createRequire } from "module";
import { readFileSync } from "fs";
import path from "path";

const require = createRequire(import.meta.url);

// drugs テーブル（PMDA）の許可リスク区分
const PMDA_ALLOWED = ["第２類医薬品", "第「２」類医薬品", "第３類医薬品"];
// jsmi_drugs テーブルの許可リスク区分
const JSMI_ALLOWED = ["第二類医薬品", "指定第二類医薬品", "第三類医薬品"];

const MAX_RESULTS = 20;

let _SQL = null;

async function getSQL() {
  if (_SQL) return _SQL;
  const initSqlJs = require("sql.js");
  _SQL = await initSqlJs({
    locateFile: (file) =>
      path.join(process.cwd(), "node_modules", "sql.js", "dist", file),
  });
  return _SQL;
}

function buildLikeClauses(keywords, cols) {
  // 各キーワード × 各カラムの OR 条件を生成
  return keywords
    .map(() => `(${cols.map((c) => `${c} LIKE ?`).join(" OR ")})`)
    .join(" OR ");
}

function buildParams(keywords, cols) {
  // LIKE パラメータ: keyword ごとに各カラム分用意
  return keywords.flatMap((kw) => cols.map(() => `%${kw}%`));
}

function runQuery(SQL, keywords) {
  const dbPath = path.join(process.cwd(), "otc_drugs.db");
  const db = new SQL.Database(readFileSync(dbPath));

  try {
    const results = [];

    // ── drugs テーブル（PMDA）──────────────────────────────
    {
      const cols = ["販売名", "効能効果", "成分"];
      const whereLike = buildLikeClauses(keywords, cols);
      const likeParams = buildParams(keywords, cols);
      const riskPH = PMDA_ALLOWED.map(() => "?").join(",");

      const sql = `
        SELECT 販売名, 製造販売会社, リスク区分, 効能効果, 成分
        FROM drugs
        WHERE (${whereLike})
          AND リスク区分 IN (${riskPH})
        ORDER BY
          CASE WHEN 販売名 LIKE ? THEN 0 WHEN 効能効果 LIKE ? THEN 1 ELSE 2 END
        LIMIT ?
      `;
      const firstLike = `%${keywords[0]}%`;
      const stmt = db.prepare(sql);
      stmt.bind([...likeParams, ...PMDA_ALLOWED, firstLike, firstLike, MAX_RESULTS]);
      while (stmt.step()) {
        const r = stmt.getAsObject();
        results.push({
          name: r["販売名"] || "",
          manufacturer: r["製造販売会社"] || "",
          risk_class: r["リスク区分"] || "",
          efficacy: r["効能効果"] || "",
          ingredients: r["成分"] || "",
          source: "drugs",
        });
      }
      stmt.free();
    }

    // ── jsmi_drugs テーブル ──────────────────────────────
    {
      const cols = ["製品名", "症状効能", "有効成分"];
      const whereLike = buildLikeClauses(keywords, cols);
      const likeParams = buildParams(keywords, cols);
      const riskPH = JSMI_ALLOWED.map(() => "?").join(",");

      const sql = `
        SELECT 製品名, メーカー名, 医薬品分類, 症状効能, 有効成分
        FROM jsmi_drugs
        WHERE (${whereLike})
          AND 医薬品分類 IN (${riskPH})
        ORDER BY
          CASE WHEN 製品名 LIKE ? THEN 0 WHEN 症状効能 LIKE ? THEN 1 ELSE 2 END
        LIMIT ?
      `;
      const firstLike = `%${keywords[0]}%`;
      const stmt = db.prepare(sql);
      stmt.bind([...likeParams, ...JSMI_ALLOWED, firstLike, firstLike, MAX_RESULTS]);
      while (stmt.step()) {
        const r = stmt.getAsObject();
        results.push({
          name: r["製品名"] || "",
          manufacturer: r["メーカー名"] || "",
          risk_class: r["医薬品分類"] || "",
          efficacy: r["症状効能"] || "",
          ingredients: r["有効成分"] || "",
          source: "jsmi_drugs",
        });
      }
      stmt.free();
    }

    return results.slice(0, MAX_RESULTS);
  } finally {
    db.close();
  }
}

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  if (req.method === "OPTIONS") return res.status(200).end();

  const { keyword, risk } = req.query;

  if (!keyword || !keyword.trim()) {
    return res.status(400).json({ error: "keyword パラメータが必要です" });
  }

  // スペース区切りで最大3キーワード
  const keywords = keyword
    .trim()
    .split(/\s+/)
    .filter((w) => w.length >= 1)
    .slice(0, 3);

  try {
    const SQL = await getSQL();
    const results = runQuery(SQL, keywords);
    return res.status(200).json({
      keywords,
      count: results.length,
      results,
    });
  } catch (e) {
    return res.status(500).json({ error: "検索失敗", detail: e.message });
  }
}
