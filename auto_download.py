#!/usr/bin/env python3
"""
Anna's Archive 自动下载脚本 v3
读取 书目搜索结果.xlsx，逐本找到真实下载链接并下载

运行：python auto_download.py
"""

import asyncio
import random
import re
from pathlib import Path

import openpyxl
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── 配置 ──────────────────────────────────────────────────────────────────────
PROXY        = "http://127.0.0.1:10808"
EXCEL_FILE   = "书目搜索结果.xlsx"
DOWNLOAD_DIR = Path("下载书籍")
DONE_FILE    = "downloaded.txt"
HEADLESS     = False
DELAY_MIN    = 3.0
DELAY_MAX    = 6.0
BASE_URL     = "https://annas-archive.gl"
# ─────────────────────────────────────────────────────────────────────────────

MIRROR_KEYWORDS = [
    "libgen.rs", "libgen.is", "libgen.li",
    "library.lol", "b-ok.", "z-lib.", "zlibrary",
    "books.ms", "ipfs", "slow_download", "sci-hub",
]
SKIP_KEYWORDS = ["/account/", "/search", "/datasets", "/db/",
                 "javascript:", "#", "/donate", "/faq", "/blog"]


def load_links():
    wb = openpyxl.load_workbook(EXCEL_FILE)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=False):
        data = {}
        for i, cell in enumerate(row):
            if i < len(headers) and headers[i]:
                data[headers[i]] = cell.hyperlink.target if cell.hyperlink else cell.value
        url  = data.get("下载链接", "") or ""
        num  = data.get("序号", "")
        name = data.get("找到书名", "") or data.get("原书名", "") or f"书_{num}"

        if not url or not isinstance(url, str):
            continue
        # 跳过无效链接
        if any(skip in url for skip in SKIP_KEYWORDS):
            continue
        if not url.startswith("http"):
            continue

        rows.append({"序号": str(num), "书名": str(name), "url": url})
    return rows


def load_done():
    if Path(DONE_FILE).exists():
        return set(Path(DONE_FILE).read_text(encoding="utf-8").splitlines())
    return set()


def mark_done(num):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(num + "\n")


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:80].strip()


async def get_mirror_links(page):
    """在 Anna's Archive 详情页找镜像下载链接"""
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(1)

    all_links = await page.query_selector_all("a[href]")
    mirrors = []
    for el in all_links:
        href = await el.get_attribute("href") or ""
        if any(skip in href for skip in SKIP_KEYWORDS):
            continue
        if any(kw in href for kw in MIRROR_KEYWORDS):
            mirrors.append(href if href.startswith("http") else BASE_URL + href)
    return mirrors


async def download_from_mirror(context, mirror_url, title):
    """在镜像站点击下载，返回保存路径或 None"""
    page = await context.new_page()
    try:
        await page.goto(mirror_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        # libgen / library.lol 下载按钮选择器
        btn_selectors = [
            "a#download",
            "a[href*='get.php']",
            "a[href*='/get/']",
            "a[href*='/download/']",
            "a.btn-success",
            "a.btn-primary",
            "a[href$='.pdf']",
            "a[href$='.epub']",
            "a[href$='.mobi']",
            "a[href$='.djvu']",
        ]
        for sel in btn_selectors:
            btns = await page.query_selector_all(sel)
            for btn in btns:
                href = await btn.get_attribute("href") or ""
                if any(skip in href for skip in SKIP_KEYWORDS):
                    continue
                try:
                    async with page.expect_download(timeout=90000) as dl_info:
                        await btn.click()
                    dl = await dl_info.value
                    ext = dl.suggested_filename.rsplit(".", 1)[-1] or "pdf"
                    save_path = DOWNLOAD_DIR / f"{safe_name(title)}.{ext}"
                    await dl.save_as(save_path)
                    return save_path
                except Exception:
                    continue
    except Exception as e:
        print(f"      镜像站出错: {e}")
    finally:
        if not page.is_closed():
            await page.close()
    return None


async def process_book(page, context, url, title):
    """处理一本书的下载流程"""

    # ── Case 1: 已经是镜像直链 ─────────────────────────────────────
    if any(kw in url for kw in MIRROR_KEYWORDS) and "/md5/" not in url:
        print(f"    → 直接镜像链接")
        result = await download_from_mirror(context, url, title)
        if result:
            print(f"    ✅ {result.name}")
            return True

    # ── Case 2: Anna's Archive 详情页 /md5/ ─────────────────────────
    if "/md5/" in url or "annas-archive" in url:
        # 确保是详情页
        if "/md5/" not in url:
            print(f"    ⚠ 非详情页 URL，跳过: {url[:60]}")
            return False

        print(f"    → 打开详情页...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.5)
        except PlaywrightTimeout:
            print(f"    ⏱ 详情页超时")
            return False

        mirrors = await get_mirror_links(page)
        if not mirrors:
            print(f"    ❌ 详情页无镜像链接")
            return False

        print(f"    找到 {len(mirrors)} 个镜像链接")
        for m_url in mirrors[:3]:
            print(f"    → 尝试: {m_url[:65]}")
            result = await download_from_mirror(context, m_url, title)
            if result:
                print(f"    ✅ {result.name}")
                return True

    print(f"    ❌ 所有方案失败")
    return False


async def main():
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    books = load_links()
    done  = load_done()
    pending = [b for b in books if b["序号"] not in done]

    print(f"📚 共 {len(books)} 条 | 已完成 {len(done)} | 待处理 {len(pending)}")
    print(f"📁 保存到: {DOWNLOAD_DIR.absolute()}\n")

    if not pending:
        print("✅ 全部完成！")
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", f"--proxy-server={PROXY}"],
        )
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/121.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 800},
            locale="zh-CN",
            accept_downloads=True,
        )
        page = await ctx.new_page()

        success = fail = 0
        for i, book in enumerate(pending):
            num, title, url = book["序号"], book["书名"], book["url"]
            print(f"[{i+1:4d}/{len(pending)}] #{num} {title[:45]}")

            ok = await process_book(page, ctx, url, title)
            if ok:
                success += 1
                mark_done(num)
            else:
                fail += 1

            await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        await browser.close()

    print(f"\n🎉 完成！成功 {success} | 失败 {fail}")


if __name__ == "__main__":
    asyncio.run(main())
