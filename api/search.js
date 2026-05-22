import { createRequire } from "module";
import { readFileSync } from "fs";
import path from "path";

const require = createRequire(import.meta.url);

const ALLOWED_RISKS = ["第２類医薬品", "第「２」類医薬品", "第３類医薬品"];
const MAX_RESULTS = 20;

// sql.js の SQL オブジェクトをウォームスタート間でキャッシュ
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

function runQuery(SQL, keyword, risk) {
  const dbPath = path.join(process.cwd(), "otc_drugs.db");
  const db = new SQL.Database(readFileSync(dbPath));

  try {
    const targetRisks =
      risk && ALLOWED_RISKS.includes(risk) ? [risk] : ALLOWED_RISKS;
    const riskPlaceholders = targetRisks.map(() => "?").join(",");
    const like = `%${keyword}%`;

    const sql = `
      SELECT 販売名, 製造販売会社, リスク区分, 効能効果, 成分, 詳細URL
      FROM drugs
      WHERE (販売名 LIKE ? OR 効能効果 LIKE ? OR 成分 LIKE ?)
        AND リスク区分 IN (${riskPlaceholders})
      ORDER BY
        CASE
          WHEN 販売名 LIKE ? THEN 0
          WHEN 効能効果 LIKE ? THEN 1
          ELSE 2
        END
      LIMIT ?
    `;

    const stmt = db.prepare(sql);
    stmt.bind([like, like, like, ...targetRisks, like, like, MAX_RESULTS]);

    const rows = [];
    while (stmt.step()) {
      rows.push(stmt.getAsObject());
    }
    stmt.free();
    return rows;
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

  try {
    const SQL = await getSQL();
    const rows = runQuery(SQL, keyword.trim(), risk);
    return res.status(200).json({
      keyword: keyword.trim(),
      risk: risk || null,
      count: rows.length,
      results: rows,
    });
  } catch (e) {
    return res.status(500).json({ error: "検索失敗", detail: e.message });
  }
}
