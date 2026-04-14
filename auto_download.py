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

# 需要拦截的书籍文件类型
FILE_TYPES = ["pdf", "epub", "mobi", "djvu", "azw3", "azw", "fb2", "cbz", "cbr"]


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

    # ── 方法一：路由拦截 ──────────────────────────────
    # 在请求到达浏览器之前拦截，直接取出 PDF/EPUB 字节存盘
    # 不依赖任何界面元素，最可靠
    saved     = asyncio.Event()
    file_info = {}

    async def intercept(route, request):
        # 只处理可能携带书籍内容的资源类型
        if request.resource_type not in ("document", "other", "fetch", "xhr", "media"):
            await route.continue_()
            return
        try:
            response = await route.fetch()
            ct      = response.headers.get("content-type", "").lower()
            req_url = request.url.lower()
            is_book = (
                any(t in ct for t in FILE_TYPES)
                or any(req_url.endswith(f".{t}") for t in FILE_TYPES)
            )
            if is_book and not saved.is_set():
                body = await response.body()
                if len(body) > 10_000:          # 至少 10 KB 才算有效文件
                    ext = next(
                        (t for t in FILE_TYPES if t in ct or req_url.endswith(f".{t}")),
                        "pdf"
                    )
                    sp = DOWNLOAD_DIR / f"{safe_name(title)}.{ext}"
                    sp.write_bytes(body)
                    file_info["path"] = sp
                    saved.set()
                    await route.abort()         # 已存盘，不再让浏览器渲染
                    return
            await route.fulfill(response=response)
        except Exception:
            try:
                await route.continue_()
            except Exception:
                pass

    await page.route("**/*", intercept)
    try:
        # wait_until="commit" 在 URL 跳转后即返回，
        # 避免因 route.abort() 导致 goto 超时
        await page.goto(url, wait_until="commit", timeout=30000)
    except Exception:
        pass  # route.abort() 可能使导航抛异常，属正常情况
    try:
        await asyncio.wait_for(saved.wait(), timeout=25)
    except asyncio.TimeoutError:
        pass
    await page.unroute("**/*", intercept)

    if "path" in file_info and file_info["path"].exists():
        size_mb = file_info["path"].stat().st_size / 1024 / 1024
        if size_mb < 0.05:
            file_info["path"].unlink(missing_ok=True)
            print(f"    ❌ 文件太小")
            return False
        print(f"    ✅ {file_info['path'].name}  ({size_mb:.1f} MB)")
        return True

    # ── 方法二：点击 PDF 查看器里的 ↓ 下载按钮（兜底）──
    # 当路由拦截未捕获到文件时（浏览器已把 PDF 渲染成查看器），
    # 通过 shadow DOM 点击右上角的下载箭头
    try:
        async with page.expect_download(timeout=20000) as dl_info:
            await page.locator("pdf-viewer").shadow_locator("#download").click()
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
