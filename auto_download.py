#!/usr/bin/env python3
"""
Anna's Archive 自动下载脚本 v6
- 如果 Excel 里有正确的 /md5/ 链接 → 直接 Fast Download
- 如果链接无效（/account/downloaded 等）→ 自动用「找到书名」重新搜索取 MD5，再 Fast Download

使用方法：
  1. 运行脚本，浏览器会自动打开
  2. 在浏览器里登录 Anna's Archive 账号（只需一次）
  3. 回到命令行按 Enter，开始全自动下载

运行：python auto_download.py
"""

import asyncio
import random
import re
from pathlib import Path
from urllib.parse import quote

import openpyxl
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── 配置 ──────────────────────────────────────────────────────────────────────
PROXY        = "http://127.0.0.1:10808"
EXCEL_FILE   = "书目搜索结果.xlsx"
DOWNLOAD_DIR = Path("下载书籍")
DONE_FILE    = "downloaded.txt"
HEADLESS     = False
DELAY_MIN    = 1.5
DELAY_MAX    = 3.0
BASE_URL     = "https://annas-archive.gl"
# ─────────────────────────────────────────────────────────────────────────────

INVALID_PATTERNS = ["/account/", "javascript:", "#", "/donate",
                    "/faq", "/blog", "/search", "/datasets"]


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def extract_md5(url: str) -> str | None:
    m = re.search(r'/md5/([a-f0-9]{32})', url, re.I)
    return m.group(1).lower() if m else None


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:80].strip()


def is_invalid_url(url: str) -> bool:
    if not url or not url.startswith("http"):
        return True
    return any(p in url for p in INVALID_PATTERNS)


# ── Excel 读取 ────────────────────────────────────────────────────────────────

def load_books() -> list[dict]:
    wb = openpyxl.load_workbook(EXCEL_FILE)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=False):
        data = {}
        for i, cell in enumerate(row):
            if i < len(headers) and headers[i]:
                data[headers[i]] = cell.hyperlink.target if cell.hyperlink else cell.value
        num        = str(data.get("序号", "") or "").strip()
        found_name = str(data.get("找到书名", "") or "").strip()
        orig_name  = str(data.get("原书名", "") or "").strip()
        url        = str(data.get("下载链接", "") or "").strip()
        if num:
            rows.append({
                "序号":     num,
                "书名":     found_name or orig_name or f"书_{num}",
                "找到书名": found_name,
                "原书名":   orig_name,
                "url":      url,
            })
    return rows


def load_done() -> set:
    if Path(DONE_FILE).exists():
        return set(Path(DONE_FILE).read_text(encoding="utf-8").splitlines())
    return set()


def mark_done(num: str):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(num + "\n")


# ── 搜索取 MD5 ────────────────────────────────────────────────────────────────

async def search_for_md5(page, title: str) -> str | None:
    """
    在 Anna's Archive 搜索书名，返回第一个结果的 MD5；找不到返回 None
    """
    query = title.strip()
    if not query:
        return None

    for lang in ["&lang=zh&sort=", ""]:
        try:
            url = f"{BASE_URL}/search?q={quote(query)}{lang}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(0.8, 1.5))

            # 找第一条 /md5/ 结果
            for sel in ["a.js-vim-focus", "div.mb-4 a[href^='/md5/']", "a[href^='/md5/']"]:
                items = await page.query_selector_all(sel)
                for item in items:
                    href = await item.get_attribute("href") or ""
                    md5 = extract_md5(href)
                    if md5:
                        return md5
        except Exception:
            continue
    return None


# ── 下载逻辑 ─────────────────────────────────────────────────────────────────

async def fast_download(page, md5: str, title: str) -> Path | None:
    """进入 /md5/ 详情页，点 Fast Download 按钮"""
    try:
        await page.goto(f"{BASE_URL}/md5/{md5}",
                        wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1.5)
    except PlaywrightTimeout:
        return None

    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(0.8)

    for sel in [
        "a[href*='/fast_download/']",
        "a:text-matches('fast download', 'i')",
        "a[href*='fast_download']",
    ]:
        try:
            btn = await page.query_selector(sel)
            if not btn:
                continue
            href = await btn.get_attribute("href") or ""
            if any(p in href for p in INVALID_PATTERNS):
                continue
            try:
                async with page.expect_download(timeout=120000) as dl_info:
                    await btn.click()
                dl = await dl_info.value
                fname = dl.suggested_filename
                ext   = fname.rsplit(".", 1)[-1] if "." in fname else "pdf"
                save_path = DOWNLOAD_DIR / f"{safe_name(title)}.{ext}"
                await dl.save_as(save_path)

                size_mb = save_path.stat().st_size / 1024 / 1024
                if size_mb < 0.05:
                    save_path.unlink(missing_ok=True)
                    return None
                print(f"    ✅ {save_path.name}  ({size_mb:.1f} MB)")
                return save_path
            except Exception:
                continue
        except Exception:
            continue
    return None


async def slow_download(page, md5: str, title: str) -> Path | None:
    """备用：/slow_download/ 页面（无需会员）"""
    try:
        await page.goto(f"{BASE_URL}/slow_download/{md5}",
                        wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
    except PlaywrightTimeout:
        return None

    for sel in ["a[href*='slow_download']", "a:text-matches('download', 'i')"]:
        try:
            btn = await page.query_selector(sel)
            if not btn:
                continue
            href = await btn.get_attribute("href") or ""
            if any(p in href for p in INVALID_PATTERNS):
                continue
            try:
                async with page.expect_download(timeout=300000) as dl_info:
                    await btn.click()
                dl = await dl_info.value
                fname = dl.suggested_filename
                ext   = fname.rsplit(".", 1)[-1] if "." in fname else "pdf"
                save_path = DOWNLOAD_DIR / f"{safe_name(title)}.{ext}"
                await dl.save_as(save_path)

                size_mb = save_path.stat().st_size / 1024 / 1024
                if size_mb < 0.05:
                    save_path.unlink(missing_ok=True)
                    return None
                print(f"    ✅ (慢速) {save_path.name}  ({size_mb:.1f} MB)")
                return save_path
            except Exception:
                continue
        except Exception:
            continue
    return None


# ── 处理单本书 ────────────────────────────────────────────────────────────────

async def process_book(page, book: dict) -> bool:
    url        = book["url"]
    title      = book["书名"]
    found_name = book["找到书名"]

    # Step 1：从 URL 里取 MD5
    md5 = extract_md5(url)

    # Step 2：URL 无效 → 用「找到书名」搜索取 MD5
    if not md5:
        if not found_name:
            print(f"    ⚠ 链接无效且无搜索结果记录，跳过")
            return False
        print(f"    → 链接无效，重新搜索「{found_name[:40]}」…")
        md5 = await search_for_md5(page, found_name)
        if not md5:
            print(f"    ❌ 搜索未找到")
            return False
        print(f"    → 找到 md5={md5[:8]}…")

    # Step 3：Fast Download
    result = await fast_download(page, md5, title)
    if result:
        return True

    # Step 4：备用慢速下载
    print(f"    → Fast Download 未触发，尝试慢速…")
    result = await slow_download(page, md5, title)
    return result is not None


# ── 主流程 ────────────────────────────────────────────────────────────────────

async def main():
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    books = load_books()
    done  = load_done()

    pending = [b for b in books if b["序号"] not in done]
    # 只处理有找到书名或有效 URL 的条目（纯未找到的跳过）
    pending = [b for b in pending if b["找到书名"] or not is_invalid_url(b["url"])]

    direct  = sum(1 for b in pending if extract_md5(b["url"]))
    need_search = len(pending) - direct

    print(f"📚 共 {len(books)} 条 | 已完成 {len(done)} | 待处理 {len(pending)}")
    print(f"   其中：直接有 MD5 = {direct} 条 | 需重新搜索 = {need_search} 条")
    print(f"📁 保存到: {DOWNLOAD_DIR.absolute()}\n")

    if not pending:
        print("✅ 全部完成！")
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                f"--proxy-server={PROXY}",
            ],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 800},
            locale="zh-CN",
            accept_downloads=True,
        )
        page = await ctx.new_page()

        # ── 登录 ─────────────────────────────────────────────────────
        print("🌐 正在打开 Anna's Archive…")
        try:
            await page.goto(f"{BASE_URL}/account",
                            wait_until="domcontentloaded", timeout=40000)
        except Exception:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=40000)

        print()
        print("=" * 60)
        print("请在浏览器窗口里登录你的 Anna's Archive 账号")
        print("登录完成后，回到这里按 Enter 开始下载")
        print("=" * 60)
        input(">>> 登录完成，按 Enter 继续：")
        print()

        success = fail = 0
        for i, book in enumerate(pending):
            num, title = book["序号"], book["书名"]
            print(f"[{i+1:4d}/{len(pending)}] #{num}  {title[:50]}")

            ok = await process_book(page, book)
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
