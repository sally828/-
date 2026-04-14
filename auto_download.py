#!/usr/bin/env python3
"""
Anna's Archive 自动下载脚本 v5（Playwright 会员快速下载版）
利用会员账号的 Fast Download 按钮，通过真实浏览器下载书籍

使用方法：
  1. 运行脚本，浏览器会自动打开
  2. 在浏览器里登录你的 Anna's Archive 账号（只需登录一次）
  3. 登录完成后回到命令行，按 Enter 开始自动下载

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
HEADLESS     = False       # 必须 False，需要你手动登录一次
DELAY_MIN    = 2.0
DELAY_MAX    = 4.0
BASE_URL     = "https://annas-archive.gl"
# ─────────────────────────────────────────────────────────────────────────────

SKIP_KEYWORDS = ["/account/", "/search", "/datasets", "/db/",
                 "javascript:", "#", "/donate", "/faq", "/blog"]


def extract_md5(url: str) -> str | None:
    """从 URL 中提取 MD5 哈希"""
    m = re.search(r'/md5/([a-f0-9]{32})', url, re.I)
    return m.group(1).lower() if m else None


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:80].strip()


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
        num  = str(data.get("序号", "") or "").strip()
        name = str(data.get("找到书名", "") or data.get("原书名", "") or f"书_{num}").strip()
        url  = str(data.get("下载链接", "") or "").strip()
        if num:
            rows.append({"序号": num, "书名": name, "url": url})
    return rows


def load_done() -> set:
    if Path(DONE_FILE).exists():
        return set(Path(DONE_FILE).read_text(encoding="utf-8").splitlines())
    return set()


def mark_done(num: str):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(num + "\n")


async def try_fast_download(page, context, md5: str, title: str) -> Path | None:
    """
    进入 /md5/ 详情页，点击 Fast Download 按钮下载文件
    成功返回保存路径，失败返回 None
    """
    detail_url = f"{BASE_URL}/md5/{md5}"
    try:
        await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1.5)
    except PlaywrightTimeout:
        print(f"    ⏱ 详情页超时")
        return None

    # 滚动到底部确保按钮加载
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(1)

    # 找 Fast Download 按钮（会员专属）
    fast_dl_selectors = [
        "a[href*='/fast_download/']",
        "a:text-matches('fast download', 'i')",
        "a:text-matches('快速下载', 'i')",
        "a[href*='fast_download']",
    ]

    for sel in fast_dl_selectors:
        try:
            btn = await page.query_selector(sel)
            if not btn:
                continue
            href = await btn.get_attribute("href") or ""
            if any(skip in href for skip in SKIP_KEYWORDS):
                continue

            print(f"    → 点击 Fast Download 按钮")
            try:
                async with page.expect_download(timeout=120000) as dl_info:
                    await btn.click()
                dl = await dl_info.value
                ext = dl.suggested_filename.rsplit(".", 1)[-1] if "." in dl.suggested_filename else "pdf"
                save_path = DOWNLOAD_DIR / f"{safe_name(title)}.{ext}"
                await dl.save_as(save_path)

                size_mb = save_path.stat().st_size / 1024 / 1024
                if size_mb < 0.05:
                    print(f"    ❌ 文件过小 ({size_mb:.2f} MB)，可能下载失败")
                    save_path.unlink(missing_ok=True)
                    return None

                print(f"    ✅ {save_path.name}  ({size_mb:.1f} MB)")
                return save_path
            except Exception:
                continue
        except Exception:
            continue

    return None


async def try_slow_download(page, context, md5: str, title: str) -> Path | None:
    """
    作为备用：使用 /slow_download/ 链接下载（无需会员，但速度慢）
    """
    slow_url = f"{BASE_URL}/slow_download/{md5}"
    try:
        await page.goto(slow_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
    except PlaywrightTimeout:
        return None

    # 找实际下载链接
    btn_selectors = [
        "a[href*='slow_download'][href$='.pdf']",
        "a[href*='slow_download'][href$='.epub']",
        "a:text-matches('download', 'i')",
        "a[href*='/slow_download/']",
    ]
    for sel in btn_selectors:
        try:
            btn = await page.query_selector(sel)
            if not btn:
                continue
            href = await btn.get_attribute("href") or ""
            if any(skip in href for skip in SKIP_KEYWORDS):
                continue
            try:
                async with page.expect_download(timeout=300000) as dl_info:
                    await btn.click()
                dl = await dl_info.value
                ext = dl.suggested_filename.rsplit(".", 1)[-1] if "." in dl.suggested_filename else "pdf"
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


async def process_book(page, context, book: dict) -> bool:
    url   = book["url"]
    title = book["书名"]

    # 提取 MD5
    md5 = extract_md5(url)
    if not md5:
        # 尝试从其他格式 URL 拿 MD5
        if url.startswith("http") and not any(s in url for s in SKIP_KEYWORDS):
            print(f"    ⚠ 无法提取 MD5，跳过: {url[:60]}")
        else:
            print(f"    ⚠ 链接无效，跳过")
        return False

    # 先尝试 Fast Download（会员专属，快）
    result = await try_fast_download(page, context, md5, title)
    if result:
        return True

    print(f"    → Fast Download 未找到，尝试慢速下载…")
    result = await try_slow_download(page, context, md5, title)
    return result is not None


async def main():
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    books = load_books()
    done  = load_done()
    all_pending = [b for b in books if b["序号"] not in done]

    # 过滤无效 URL（/account/ 等）
    pending = []
    skip_count = 0
    for b in all_pending:
        url = b["url"]
        md5 = extract_md5(url)
        if md5:
            pending.append(b)
        else:
            skip_count += 1

    print(f"📚 共 {len(books)} 条 | 已完成 {len(done)} | 可下载 {len(pending)} | 跳过(无效链接) {skip_count}")
    print(f"📁 保存到: {DOWNLOAD_DIR.absolute()}\n")

    if not pending:
        print("✅ 全部完成！（或所有剩余书目链接无效，请重新运行 search_anna.py）")
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

        # ── 登录步骤 ─────────────────────────────────────────────────
        print("🌐 正在打开 Anna's Archive 账户页面…")
        try:
            await page.goto(f"{BASE_URL}/account", wait_until="domcontentloaded", timeout=40000)
        except Exception:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=40000)

        print()
        print("=" * 60)
        print("请在弹出的浏览器窗口里登录你的 Anna's Archive 账号")
        print("登录完成后，回到这里按 Enter 开始下载")
        print("=" * 60)
        input(">>> 登录完成，按 Enter 继续：")
        print()

        success = fail = 0
        for i, book in enumerate(pending):
            num, title = book["序号"], book["书名"]
            print(f"[{i+1:4d}/{len(pending)}] #{num}  {title[:50]}")

            ok = await process_book(page, ctx, book)
            if ok:
                success += 1
                mark_done(num)
            else:
                fail += 1

            await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        await browser.close()

    print(f"\n🎉 完成！成功 {success} | 失败 {fail}")
    if fail:
        print("💡 失败的书可能没有 Fast Download 按钮，或需要等限额刷新后重试")


if __name__ == "__main__":
    asyncio.run(main())
