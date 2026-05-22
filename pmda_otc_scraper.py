"""
PMDA OTC薬品スクレイパー
https://www.pmda.go.jp/PmdaSearch/otcSearch/

使用ライブラリ: playwright (pip install playwright && playwright install chromium)

実行:
    python pmda_otc_scraper.py               # 一覧のみ取得
    python pmda_otc_scraper.py --debug        # 構造確認モード（'ア'のみ）
    python pmda_otc_scraper.py --output out.csv
"""

import csv
import re
import sys
import time
import json
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Tuple
from html.parser import HTMLParser
from playwright.sync_api import sync_playwright, Page, BrowserContext

BASE_URL = "https://www.pmda.go.jp/PmdaSearch/otcSearch/"
DELAY = 1.5      # 検索間の待機秒数
PAGE_DELAY = 1.0  # ページ間の待機秒数
OUTPUT_FILE = "pmda_otc_drugs.csv"

# 前方一致検索で全OTC薬品をカバーするための最初の文字リスト
# カタカナ（濁音・半濁音・小文字含む）+ アルファベット + 数字
SEARCH_CHARS = list(
    "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"
    "ァィゥェォッャュョ"
    "ガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポ"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
)


# ─── HTML パーサー ────────────────────────────────────────────────────────

class ResultListParser(HTMLParser):
    """ResultList HTMLから薬品データを抽出するパーサー。"""

    def __init__(self):
        super().__init__()
        self.drugs: List[Dict] = []
        self._in_row = False
        self._in_td = False
        self._td_count = 0
        self._current: Dict = {}
        self._link_href = ""
        self._in_link = False
        self._text_buf = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "tr":
            cls = attrs_dict.get("class", "")
            if "TrColor" in cls or "TrColor01" in cls or "TrColor02" in cls:
                self._in_row = True
                self._td_count = 0
                self._current = {}
        elif tag == "td" and self._in_row:
            self._in_td = True
            self._text_buf = ""
            self._td_count += 1
            self._link_href = ""
        elif tag == "a" and self._in_td:
            href = attrs_dict.get("href", "")
            if href and href.startswith("/PmdaSearch/otcDetail/"):
                self._link_href = f"https://www.pmda.go.jp{href}"
            self._in_link = True

    def handle_endtag(self, tag):
        if tag == "tr" and self._in_row:
            if self._current.get("販売名"):
                self.drugs.append(self._current)
            self._in_row = False
            self._current = {}
        elif tag == "td" and self._in_row:
            text = self._text_buf.strip()
            if self._td_count == 1:
                self._current["販売名"] = text
                if self._link_href:
                    self._current["詳細URL"] = self._link_href
            elif self._td_count == 2:
                self._current["製造販売会社"] = text
            self._in_td = False
            self._in_link = False

    def handle_data(self, data):
        if self._in_td:
            self._text_buf += data


def parse_result_list(html: str) -> List[Dict]:
    """ResultList HTMLから薬品データを抽出する。"""
    parser = ResultListParser()
    parser.feed(html)
    return parser.drugs


def parse_total_pages(page_navi: str) -> int:
    """PageNavi HTMLから総ページ数を取得する。"""
    m = re.search(r"全(\d+)ページ", page_navi)
    if m:
        return int(m.group(1))
    return 1


# ─── 検索実行 ────────────────────────────────────────────────────────────

def search_by_prefix(
    context: BrowserContext,
    search_page: Page,
    char: str,
    debug: bool = False,
) -> List[Dict]:
    """
    指定文字で前方一致検索し、全ページのデータを返す。
    検索ごとに新しいポップアップを開く。
    """
    results: List[Dict] = []

    # 検索フォームに文字を設定
    search_page.evaluate(
        f"document.querySelector('[name=nameWord]').value = '{char}'"
    )
    # 前方一致 (howtoMatchRadioValue=2)
    search_page.evaluate(
        "document.querySelectorAll('[name=howtoMatchRadioValue]')"
        ".forEach(r => r.checked = r.value === '2')"
    )

    # ポップアップを開く（失敗時は最大2回リトライ）
    popup = None
    for attempt in range(3):
        try:
            with context.expect_page(timeout=20_000) as popup_info:
                search_page.click("input[name='btnA']")
            popup = popup_info.value
            try:
                popup.wait_for_load_state("networkidle", timeout=30_000)
            except Exception:
                pass
            time.sleep(0.8)
            # 正しい結果ページかチェック
            if "otcSearch" in popup.url and popup.evaluate("() => typeof changePg") == "function":
                break
            popup.close()
            popup = None
            time.sleep(1.5)
        except Exception as e:
            if attempt == 2:
                print(f"    [警告] '{char}' ポップアップ失敗: {e}")
            time.sleep(2.0)

    if popup is None:
        return results

    # 全ページを取得
    page_num = 1
    total_pages = 1
    while page_num <= total_pages:
        try:
            with popup.expect_response(
                lambda r: "PageChangeRequest" in r.url,
                timeout=20_000,
            ) as resp_info:
                popup.evaluate(f"() => changePg({page_num})")
            resp = resp_info.value
        except Exception as e:
            print(f"    [警告] '{char}' p{page_num} AJAX失敗: {e}")
            break

        if resp.status != 200:
            print(f"    [警告] '{char}' p{page_num} HTTP {resp.status}")
            break

        try:
            data = json.loads(resp.body())
        except Exception as e:
            print(f"    [警告] '{char}' p{page_num} JSON解析失敗: {e}")
            break

        if page_num == 1:
            total_pages = parse_total_pages(data.get("PageNavi", ""))
            if debug:
                print(f"    総ページ数: {total_pages}")
                print(f"    ResultList sample: {data.get('ResultList', '')[:300]}")

        page_drugs = parse_result_list(data.get("ResultList", ""))
        results.extend(page_drugs)

        if debug:
            print(f"    p{page_num}/{total_pages}: {len(page_drugs)} 件")

        if page_num >= total_pages:
            break
        page_num += 1
        time.sleep(PAGE_DELAY)

    popup.close()
    return results


# ─── Phase 1: 一覧取得 ───────────────────────────────────────────────────

def scrape_list(debug: bool = False) -> List[Dict]:
    """全OTC薬品の基本情報を取得する。"""
    all_drugs: List[Dict] = []
    seen_names: set = set()

    chars_to_search = SEARCH_CHARS[:1] if debug else SEARCH_CHARS

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-popup-blocking"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        search_page = context.new_page()

        print(f"[Phase 1] 検索ページ読み込み: {BASE_URL}")
        search_page.goto(BASE_URL, wait_until="networkidle", timeout=60_000)
        time.sleep(1.0)

        # 100件表示に設定
        search_page.select_option("#ListRows", "100")
        time.sleep(0.3)

        print(f"[Phase 1] 検索開始 ({len(chars_to_search)} 文字)")
        for i, char in enumerate(chars_to_search, 1):
            print(f"  [{i}/{len(chars_to_search)}] '{char}' を検索中...")
            drugs = search_by_prefix(context, search_page, char, debug=debug)

            new_count = 0
            for drug in drugs:
                name = drug.get("販売名", "")
                if name and name not in seen_names:
                    seen_names.add(name)
                    all_drugs.append(drug)
                    new_count += 1

            print(f"    → 新規 {new_count} 件（累計: {len(all_drugs)} 件）")

            if not debug:
                time.sleep(DELAY)

        browser.close()

    return all_drugs


# ─── Phase 2: 詳細情報の取得（成分・効能・リスク区分） ─────────────────

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _generallist_to_insert_url(general_url: str) -> str:
    """GeneralList URL → 実際の添付文書 URL に変換する。
    /PmdaSearch/otcDetail/GeneralList/{code} → /PmdaSearch/otcDetail/{code}
    """
    return general_url.replace("/otcDetail/GeneralList/", "/otcDetail/")


def _clean_html(raw: str) -> str:
    """HTMLタグ・余白を除去してテキストを返す。"""
    text = re.sub(r"<!--[^>]*-->", "", raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_section(html: str, marker: str) -> str:
    """HTMLコメントマーカーの直後にある <td class="deta"> の内容を返す。"""
    idx = html.find(marker)
    if idx < 0:
        return ""
    tail = html[idx:]
    m = re.search(r"-->(.*?)(?=<td class=\"head\"|</table>)", tail, re.DOTALL)
    if not m:
        return ""
    return _clean_html(m.group(1))[:600]


def fetch_detail(general_url: str) -> Dict:
    """添付文書ページから成分・効能・リスク区分を取得する。"""
    import urllib.request

    url = _generallist_to_insert_url(general_url)
    try:
        req = urllib.request.Request(url, headers=_HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"成分": "", "効能効果": "", "リスク区分": "", "_error": str(e)}

    # 成分分量テーブルをパース（<th>成分</th><th>分量</th> テーブル）
    seibun = ""
    m = re.search(r"<!--_2000INGREDIENT-QUANTITY-->(.*?)(?=<!--_|\Z)", html, re.DOTALL)
    if m:
        block = m.group(1)
        pairs = re.findall(r"<td>([^<]+)</td>\s*<td>([^<]+)</td>", block)
        seibun = " / ".join(f"{name} {amt}" for name, amt in pairs if name.strip())
        if not seibun:
            seibun = _clean_html(block)[:400]

    kouno = _extract_section(html, "<!--_1400EFFECT-AND-AN-EFFECT-->")
    risk = _extract_section(html, "<!--_3900RISK-CLASSIFICATION-->")

    return {"成分": seibun[:500], "効能効果": kouno, "リスク区分": risk}


def enrich_with_details(
    drugs: List[Dict],
    limit: Optional[int] = None,
    checkpoint_file: Optional[str] = None,
    checkpoint_every: int = 500,
    max_workers: int = 3,
) -> List[Dict]:
    """各薬品の添付文書ページから成分・効能・リスク区分を補完する（並列取得）。"""
    targets = [d for d in drugs if d.get("詳細URL") and not d.get("成分")]
    if limit:
        targets = targets[:limit]
    if not targets:
        print("[Phase 2] 対象がありません。スキップします。")
        return drugs

    total = len(targets)
    print(f"[Phase 2] 詳細情報取得開始: {total} 件（並列数: {max_workers}）")

    lock = threading.Lock()
    completed_count = [0]

    def fetch_one(drug: Dict) -> tuple:
        info = fetch_detail(drug["詳細URL"])
        time.sleep(1.0)
        return drug, info

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, drug): drug for drug in targets}
        for future in as_completed(futures):
            try:
                drug, info = future.result()
            except Exception as e:
                drug = futures[future]
                info = {"成分": "", "効能効果": "", "リスク区分": "", "_error": str(e)}

            drug.update({k: v for k, v in info.items() if not k.startswith("_")})

            with lock:
                completed_count[0] += 1
                i = completed_count[0]
                err = info.get("_error", "")
                name = drug["販売名"][:25].encode("utf-8", errors="replace").decode("utf-8")
                if err:
                    print(f"  [{i}/{total}] {name} → エラー: {err}", file=sys.stderr)
                else:
                    seibun = (info["成分"][:30] or "(なし)").encode("utf-8", errors="replace").decode("utf-8")
                    risk = info["リスク区分"][:10].encode("utf-8", errors="replace").decode("utf-8")
                    print(f"  [{i}/{total}] {name} | リスク:{risk} | 成分:{seibun}")

                if checkpoint_file and i % checkpoint_every == 0:
                    save_csv(drugs, checkpoint_file, include_details=True)
                    print(f"  [チェックポイント] {i}/{total} 件完了 → {checkpoint_file} に保存")

    return drugs


# ─── CSV保存 ─────────────────────────────────────────────────────────────

def save_csv(drugs: List[Dict], output_file: str, include_details: bool):
    if not drugs:
        print("データが取得できませんでした。")
        return

    fields = ["販売名", "製造販売会社", "詳細URL"]
    if include_details:
        fields += ["リスク区分", "効能効果", "成分"]

    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(drugs)

    print(f"\n[OK] {len(drugs)} 件を {output_file} に保存しました")


# ─── エントリポイント ────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="PMDA OTC薬品スクレイパー")
    parser.add_argument(
        "--ingredients",
        action="store_true",
        help="全薬品の詳細ページから成分・効能・リスク区分を取得する",
    )
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="既存CSVを読み込んで全件詳細取得する（Phase 1スキップ）",
    )
    parser.add_argument(
        "--test-run",
        action="store_true",
        help="既存CSVの最初の100件だけ詳細取得してテスト出力する",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="'ア' のみで動作確認するデバッグモード",
    )
    parser.add_argument(
        "--input",
        default="pmda_otc_drugs_full.csv",
        help="--from-csv / --test-run 時の入力CSVファイル名",
    )
    parser.add_argument(
        "--output",
        default=OUTPUT_FILE,
        help=f"出力CSVファイル名（デフォルト: {OUTPUT_FILE}）",
    )
    args = parser.parse_args()

    # --test-run: 既存CSVから100件だけ詳細取得
    if args.test_run:
        import csv as _csv
        print(f"[test-run] {args.input} から最初の100件を読み込みます...")
        with open(args.input, "r", encoding="utf-8-sig") as f:
            drugs = list(_csv.DictReader(f))
        print(f"  読み込み: {len(drugs)} 件 → 最初の100件を対象にします")
        drugs_100 = drugs[:100]
        enrich_with_details(drugs_100, limit=100)
        out = args.output if args.output != OUTPUT_FILE else "pmda_test_run.csv"
        save_csv(drugs_100, out, include_details=True)
        return

    # --from-csv: 既存CSVから全件読み込んで詳細取得（Phase 1スキップ）
    if args.from_csv:
        import csv as _csv
        out = args.output if args.output != OUTPUT_FILE else args.input
        print(f"[from-csv] {args.input} から全件読み込みます...")
        with open(args.input, "r", encoding="utf-8-sig") as f:
            drugs = list(_csv.DictReader(f))
        already = sum(1 for d in drugs if d.get("成分"))
        print(f"  読み込み: {len(drugs)} 件（取得済み: {already} 件、残り: {len(drugs) - already} 件）")
        enrich_with_details(drugs, checkpoint_file=out, max_workers=3)
        save_csv(drugs, out, include_details=True)
        return

    drugs = scrape_list(debug=args.debug)

    if not drugs:
        print("\n[警告] データが取得できませんでした。")
        sys.exit(1)

    print(f"\n[Phase 1] 完了: {len(drugs)} 件取得")

    if args.ingredients:
        checkpoint = args.output.replace(".csv", "_checkpoint.csv")
        enrich_with_details(drugs, checkpoint_file=checkpoint)

    save_csv(drugs, args.output, include_details=args.ingredients)


if __name__ == "__main__":
    main()
