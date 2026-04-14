#!/usr/bin/env python3
"""
Anna's Archive 自动下载脚本（Playwright 版）
- 浏览器打开后你手动登录一次，后续全自动
- 运行：python auto_download.py
"""

import asyncio
import random
import re
from pathlib import Path

import openpyxl
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── 配置 ──────────────────────────────────────────────
PROXY        = "http://127.0.0.1:10808"   # TUN全局模式改成 None
HEADLESS     = False
DELAY_MIN    = 1.5
DELAY_MAX    = 3.0
BASE_URL     = "https://annas-archive.gl"
# ─────────────────────────────────────────────────────

_HERE        = Path(__file__).parent
EXCEL_FILE   = _HERE / "书目搜索结果.xlsx"
DOWNLOAD_DIR = _HERE / "下载书籍"
DONE_FILE    = _HERE / "downloaded.txt"


def extract_md5(url: str) -> str | None:
    m = re.search(r'[a-f0-9]{32}', str(url or ""), re.I)
    return m.group(0).lower() if m else None

def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:80].strip()

def load_done() -> set:
    return set(DONE_FILE.read_text(encoding="utf-8").splitlines()) if DONE_FILE.exists() else set()

def mark_done(num: str):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(num + "\n")

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
        name = str(data.get("找到书名", "") or data.get("原书名", "") or "").strip()
        url  = str(data.get("下载链接", "") or "").strip()
        if num:
            rows.append({"序号": num, "书名": name or f"书_{num}", "url": url})
    return rows


async def download_book(page, book: dict) -> bool:
    url   = book["url"]
    title = book["书名"]

    if not url.startswith("http"):
        print(f"    ⚠ 无链接，跳过")
        return False

    try:
        async with page.expect_download(timeout=60000) as dl_info:
            await page.goto(url, wait_until="commit", timeout=30000)
        dl = await dl_info.value
        fname = dl.suggested_filename or f"{safe_name(title)}.pdf"
        ext   = fname.rsplit(".", 1)[-1] if "." in fname else "pdf"
        save_path = DOWNLOAD_DIR / f"{safe_name(title)}.{ext}"
        await dl.save_as(save_path)
        size_mb = save_path.stat().st_size / 1024 / 1024
        if size_mb < 0.05:
            save_path.unlink(missing_ok=True)
            print(f"    ❌ 文件太小")
            return False
        print(f"    ✅ {save_path.name}  ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        print(f"    ❌ {e}")
        return False


async def main():
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    books   = load_books()
    done    = load_done()
    pending = [b for b in books if b["序号"] not in done and extract_md5(b["url"])]

    print(f"📚 共 {len(books)} 条 | 已完成 {len(done)} | 本次下载 {len(pending)} 条")
    print(f"📁 保存到: {DOWNLOAD_DIR}\n")
    if not pending:
        print("✅ 全部完成！")
        return

    async with async_playwright() as pw:
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-pdf-viewer",   # 强制 PDF 触发下载而非在浏览器内打开
            "--disable-plugins",
        ]
        if PROXY:
            args.append(f"--proxy-server={PROXY}")

        browser = await pw.chromium.launch(headless=HEADLESS, args=args)
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

        print("🌐 正在打开 Anna's Archive…")
        await page.goto(f"{BASE_URL}/account", wait_until="domcontentloaded", timeout=40000)
        print("\n" + "=" * 55)
        print("请在浏览器里登录你的 Anna's Archive 账号")
        print("登录完成后回到这里按 Enter")
        print("=" * 55)
        input(">>> 登录完成，按 Enter 开始下载：")
        print()

        # 告诉 Chrome：所有文件强制下载，不要在浏览器里打开（包括 PDF）
        cdp = await ctx.new_cdp_session(page)
        await cdp.send("Browser.setDownloadBehavior", {
            "behavior":      "allow",
            "downloadPath":  str(DOWNLOAD_DIR.absolute()),
            "eventsEnabled": True,
        })

        success = fail = 0
        for i, book in enumerate(pending):
            print(f"[{i+1:4d}/{len(pending)}] #{book['序号']}  {book['书名'][:50]}")
            ok = await download_book(page, book)
            if ok:
                success += 1
                mark_done(book["序号"])
            else:
                fail += 1
            await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        await browser.close()

    print(f"\n🎉 完成！成功 {success} | 失败 {fail}")


if __name__ == "__main__":
    asyncio.run(main())
