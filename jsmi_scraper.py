#!/usr/bin/env python3
"""
jsmi_scraper.py
日本OTC医薬品協会「おくすり検索」スクレイパー
対象: https://search.jsm-db.info/

取得項目:
  一覧ページから: 製品名, メーカー名, 薬効分類, 剤形, 医薬品分類, 症状・効能, JANコード
  詳細ページから: 有効成分
出力: jsmi_drugs.csv (UTF-8 BOM付き)
"""

import re
import csv
import sys
import time
import logging
from pathlib import Path

from scrapling.fetchers import Fetcher

# ──────────────────────────────────────────
# 設定
# ──────────────────────────────────────────
BASE_URL   = "https://search.jsm-db.info"
RESULT_URL = f"{BASE_URL}/result.php"
DETAIL_URL = f"{BASE_URL}/detail01.php"
OUTPUT_CSV = Path(__file__).parent / "jsmi_drugs.csv"
DELAY      = 1.5   # リクエスト間隔（秒）
PAGE_SIZE  = 50    # 1ページあたりの件数

# 有効成分取得のため詳細ページも取得するか（True=取得, False=スキップ）
FETCH_DETAIL = True

FIELDNAMES = [
    "製品名", "メーカー名", "薬効分類", "剤形",
    "医薬品分類", "症状・効能", "有効成分", "JANコード",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent / "jsmi_scraper.log",
                            encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# result.php の共通ヘッダー
HEADERS_RESULT = {
    "Referer":      "https://search.jsm-db.info/main2.php",
    "Content-Type": "application/x-www-form-urlencoded",
}
HEADERS_DETAIL = {
    "Referer":      RESULT_URL,
    "Content-Type": "application/x-www-form-urlencoded",
}


# ──────────────────────────────────────────
# HTML 前処理ヘルパー
# ──────────────────────────────────────────

def strip_tags(html: str) -> str:
    """HTMLタグを除去してテキスト化"""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ──────────────────────────────────────────
# HTTP リクエスト
# ──────────────────────────────────────────

def fetch_list_page(skip: int, total: int = 0) -> str:
    """
    一覧ページを取得してHTML文字列を返す。
    1ページ目は txtAct=0 (main2.php 由来)、
    2ページ目以降は txtAct=1 (result.php 内ナビゲーション由来)。
    """
    if skip == 0:
        # 初回検索
        data = {
            "txtBrandName": "", "txtEffect": "", "txtGroupNameJ": "",
            "hdnGroupNameSearch": "", "txtJicfsName": "", "txtComponent": "",
            "txtAdditives": "", "txtTablet": "", "txtJanCode": "",
            "txtAct": "0", "txtAll": "0", "txtSkip": "0", "txtDisp": "0",
        }
        headers = HEADERS_RESULT
    else:
        # ページネーション（result.php 内フォームの再送信）
        data = {
            "txtBrandName": "", "txtEffect": "", "txtGroupNameJ": "",
            "hdnGroupNameSearch": "", "txtJicfsName": "", "txtComponent": "",
            "txtAdditives": "", "txtTablet": "", "txtJanCode": "",
            "txtAct": "1", "txtAll": str(total), "txtSkip": str(skip),
            "txtDisp": str(PAGE_SIZE), "txtCond": "", "txtID": "",
        }
        headers = HEADERS_DETAIL  # Referer = result.php

    page = Fetcher.post(RESULT_URL, data=data, headers=headers)
    return str(page.body)


def fetch_detail_page(jan_code: str, total: int) -> str:
    """詳細ページのHTML文字列を返す"""
    data = {
        "txtBrandName": "", "txtEffect": "", "txtGroupNameJ": "",
        "hdnGroupNameSearch": "", "txtJicfsName": "", "txtComponent": "",
        "txtAdditives": "", "txtTablet": "", "txtJanCode": "",
        "txtAct": "1", "txtAll": str(total), "txtSkip": "0",
        "txtDisp": str(PAGE_SIZE), "txtCond": "", "txtID": jan_code,
    }
    page = Fetcher.post(DETAIL_URL, data=data, headers=HEADERS_DETAIL)
    return str(page.body)


# ──────────────────────────────────────────
# 一覧ページ解析
# ──────────────────────────────────────────

def parse_total(html: str) -> int:
    """総件数（txtAll）を取得"""
    # hidden input の txtAll value から取得（最も確実）
    m = re.search(r'name="txtAll"[^>]*value="(\d+)"', html)
    if m:
        return int(m.group(1))
    # フォールバック: 「N件中」テキストから
    m = re.search(r"([\d,]+)件中", html)
    return int(m.group(1).replace(",", "")) if m else 0


# 製品行パターン（2行1セットで1製品）
# 行1: jsSubmit("JAN") → 製品名, 剤形, メーカー名, 薬効分類, 医薬品分類
# 行2: colspan="5" → 症状・効能
_RE_PRODUCT_BLOCK = re.compile(
    r'jsSubmit\("([^"]+)"\)\'?>([^<]+)</a>'   # (1)JAN, (2)製品名
    r'.*?class="jp3"[^>]*>([^<]*)</td>'       # (3)剤形
    r'.*?class="jp3"[^>]*>([^<]*)</td>'       # (4)メーカー名
    r'.*?class="jp3"[^>]*>([^<]*)</td>'       # (5)薬効分類
    r'.*?class="jp3"[^>]*>([^<]*)</td>'       # (6)医薬品分類
    r'.*?</tr>'
    r'.*?<td[^>]*colspan[^>]*>([^<]*)</td>',  # (7)症状・効能
    re.DOTALL,
)


def parse_list_page(html: str) -> list[dict]:
    """一覧ページから全製品情報を抽出"""
    products = []
    for m in _RE_PRODUCT_BLOCK.finditer(html):
        jan_code     = m.group(1).strip()
        product_name = m.group(2).strip()
        dosage_form  = m.group(3).strip()
        maker        = m.group(4).strip()
        category     = m.group(5).strip()
        otc_class    = m.group(6).strip()
        symptoms     = m.group(7).strip()

        products.append({
            "JANコード":  jan_code,
            "製品名":     product_name,
            "剤形":       dosage_form,
            "メーカー名": maker,
            "薬効分類":   category,
            "医薬品分類": otc_class,
            "症状・効能": symptoms,
        })
    return products


# ──────────────────────────────────────────
# 詳細ページ解析
# ──────────────────────────────────────────

def parse_ingredient(html: str) -> str:
    """詳細ページから「成分・分量」（section #5）を抽出"""
    m = re.search(r'<a name="5"[^>]*>(.*?)<a name="6"', html, re.DOTALL)
    if not m:
        return ""
    text = strip_tags(m.group(1))
    # 先頭の見出し文字列とナビリンクを除去
    text = re.sub(r"^成分[\s・･]?分量\s*", "", text)
    text = re.sub(r"このページのトップへ[▲△]?\s*", "", text)
    return text.strip()


# ──────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────

def main():
    log.info("=== jsmi_scraper 開始 ===")
    log.info(f"出力先: {OUTPUT_CSV}")
    log.info(f"詳細ページ取得: {'ON' if FETCH_DETAIL else 'OFF'}")

    # ── 1. 1ページ目を取得して総件数を確認 ──
    log.info("1ページ目を取得中...")
    first_html = fetch_list_page(skip=0)
    total = parse_total(first_html)
    log.info(f"総件数: {total} 件")

    if total == 0:
        log.error("総件数が取得できませんでした。終了します。")
        sys.exit(1)

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    log.info(f"総ページ数: {total_pages}")

    # ── 2. CSV を開いて逐次書き込み ──
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()

        for page_num in range(total_pages):
            skip = page_num * PAGE_SIZE
            log.info(f"[{page_num + 1}/{total_pages}] 一覧取得 (skip={skip})")

            # 2-a. 一覧ページ取得（1ページ目は再利用）
            if page_num == 0:
                list_html = first_html
            else:
                time.sleep(DELAY)
                list_html = fetch_list_page(skip=skip, total=total)

            products = parse_list_page(list_html)
            if not products:
                log.warning(f"  ページ {page_num + 1}: 製品が抽出できませんでした。")
                continue
            log.info(f"  → {len(products)} 件取得")

            # 2-b. 詳細ページから有効成分を取得
            for i, product in enumerate(products, 1):
                jan = product["JANコード"]

                if FETCH_DETAIL:
                    log.info(
                        f"  [{i}/{len(products)}] 詳細取得: "
                        f"{product['製品名']} (JAN={jan})"
                    )
                    time.sleep(DELAY)
                    try:
                        detail_html = fetch_detail_page(jan, total)
                        product["有効成分"] = parse_ingredient(detail_html)
                    except Exception as e:
                        log.error(f"  詳細取得エラー (JAN={jan}): {e}")
                        product["有効成分"] = ""
                else:
                    product["有効成分"] = ""

                # FIELDNAMES の順序で行を書き込み
                row = {k: product.get(k, "") for k in FIELDNAMES}
                writer.writerow(row)

            f.flush()  # ページ単位で即時書き込み（中断時のデータ保護）

    log.info(f"=== 完了: {OUTPUT_CSV} ===")


if __name__ == "__main__":
    main()
