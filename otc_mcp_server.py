# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mcp",
# ]
# ///
"""
OTC薬品データベース MCPサーバー

実行:
    uv run otc_mcp_server.py

Claude Code への登録:
    claude mcp add otc-drugs -- uv run C:/Users/acey-/dev/projects/drugstore-app/otc_mcp_server.py
"""

from pathlib import Path
import sqlite3
from mcp.server.fastmcp import FastMCP

DB_PATH = Path(__file__).parent / "otc_drugs.db"
MAX_RESULTS = 20

mcp = FastMCP("OTC薬品データベース")


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _fmt(row: sqlite3.Row) -> str:
    kouno = (row["効能効果"] or "")[:200]
    if len(row["効能効果"] or "") > 200:
        kouno += "…"
    seibun = (row["成分"] or "")[:150]
    if len(row["成分"] or "") > 150:
        seibun += "…"
    return (
        f"【{row['販売名']}】\n"
        f"  リスク区分  : {row['リスク区分']}\n"
        f"  製造販売会社: {row['製造販売会社']}\n"
        f"  効能効果    : {kouno}\n"
        f"  成分        : {seibun}\n"
        f"  詳細URL     : {row['詳細URL']}"
    )


@mcp.tool()
def search_by_symptom(keyword: str, risk_category: str = "") -> str:
    """症状・効能キーワードで市販薬を検索します。

    Args:
        keyword: 検索キーワード（例: 頭痛、花粉症、胃痛、鼻水、解熱）
        risk_category: リスク区分で絞り込む場合に指定（例: 第２類医薬品、第３類医薬品）。省略で全区分。
    """
    con = _connect()
    try:
        if risk_category:
            rows = con.execute(
                "SELECT * FROM drugs WHERE 効能効果 LIKE ? AND リスク区分 = ? LIMIT ?",
                (f"%{keyword}%", risk_category, MAX_RESULTS),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM drugs WHERE 効能効果 LIKE ? LIMIT ?",
                (f"%{keyword}%", MAX_RESULTS),
            ).fetchall()
    finally:
        con.close()

    if not rows:
        return f"「{keyword}」に一致する薬は見つかりませんでした。"

    header = f"「{keyword}」の検索結果: {len(rows)} 件"
    if len(rows) == MAX_RESULTS:
        header += f"（最大 {MAX_RESULTS} 件を表示）"
    return header + "\n\n" + "\n\n".join(_fmt(r) for r in rows)


@mcp.tool()
def search_by_ingredient(ingredient_name: str, risk_category: str = "") -> str:
    """成分名で市販薬を検索します。

    Args:
        ingredient_name: 成分名（例: イブプロフェン、ロキソプロフェン、セチリジン、アセトアミノフェン）
        risk_category: リスク区分で絞り込む場合に指定（例: 第２類医薬品、第３類医薬品）。省略で全区分。
    """
    con = _connect()
    try:
        if risk_category:
            rows = con.execute(
                "SELECT * FROM drugs WHERE 成分 LIKE ? AND リスク区分 = ? LIMIT ?",
                (f"%{ingredient_name}%", risk_category, MAX_RESULTS),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM drugs WHERE 成分 LIKE ? LIMIT ?",
                (f"%{ingredient_name}%", MAX_RESULTS),
            ).fetchall()
    finally:
        con.close()

    if not rows:
        return f"成分「{ingredient_name}」を含む薬は見つかりませんでした。"

    header = f"成分「{ingredient_name}」の検索結果: {len(rows)} 件"
    if len(rows) == MAX_RESULTS:
        header += f"（最大 {MAX_RESULTS} 件を表示）"
    return header + "\n\n" + "\n\n".join(_fmt(r) for r in rows)


@mcp.tool()
def filter_by_risk_category(risk_category: str, keyword: str = "", limit: int = 20) -> str:
    """リスク区分で薬を絞り込みます。症状キーワードと組み合わせることもできます。

    Args:
        risk_category: リスク区分（第２類医薬品 / 第「２」類医薬品 / 第３類医薬品 / なし / 要指導医薬品）
        keyword: 効能効果をさらに絞り込むキーワード（省略可）
        limit: 表示件数の上限（デフォルト20、最大50）
    """
    limit = min(limit, 50)
    con = _connect()
    try:
        if keyword:
            total = con.execute(
                "SELECT COUNT(*) FROM drugs WHERE リスク区分 = ? AND 効能効果 LIKE ?",
                (risk_category, f"%{keyword}%"),
            ).fetchone()[0]
            rows = con.execute(
                "SELECT * FROM drugs WHERE リスク区分 = ? AND 効能効果 LIKE ? LIMIT ?",
                (risk_category, f"%{keyword}%", limit),
            ).fetchall()
        else:
            total = con.execute(
                "SELECT COUNT(*) FROM drugs WHERE リスク区分 = ?",
                (risk_category,),
            ).fetchone()[0]
            rows = con.execute(
                "SELECT * FROM drugs WHERE リスク区分 = ? LIMIT ?",
                (risk_category, limit),
            ).fetchall()
    finally:
        con.close()

    if not rows:
        return (
            f"リスク区分「{risk_category}」の薬は見つかりませんでした。\n"
            "利用可能な区分: 第２類医薬品 / 第「２」類医薬品 / 第３類医薬品 / なし / 要指導医薬品"
        )

    kw_label = f"・キーワード「{keyword}」" if keyword else ""
    header = f"リスク区分「{risk_category}」{kw_label}: 全 {total} 件中 {len(rows)} 件を表示"
    return header + "\n\n" + "\n\n".join(_fmt(r) for r in rows)


if __name__ == "__main__":
    mcp.run()
