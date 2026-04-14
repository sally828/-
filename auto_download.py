#!/usr/bin/env python3
"""
Anna's Archive 自动下载脚本
读取 书目搜索结果.xlsx，逐本访问链接，自动点击下载

运行前确认：
  1. V2RayN 代理已开启
  2. 书目搜索结果.xlsx 在同一文件夹
  3. 运行：python auto_download.py
"""

import asyncio
import random
import re
from pathlib import Path

import openpyxl
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── 配置 ──────────────────────────────────────────────────────────────────────
PROXY        = "http://127.0.0.1:10808"   # V2RayN 端口，和之前一样
EXCEL_FILE   = "书目搜索结果.xlsx"
DOWNLOAD_DIR = Path("下载书籍")            # 下载保存的文件夹
DONE_FILE    = "downloaded.txt"            # 记录已下载，支持断点续传
HEADLESS     = False                       # False = 显示浏览器
DELAY_MIN    = 3.0
DELAY_MAX    = 6.0

# 只下载这些格式（空列表 = 全部下载）
ALLOW_FORMATS = ["pdf", "epub", "mobi", "djvu"]
# ─────────────────────────────────────────────────────────────────────────────


def load_links() -> list[dict]:
    """从 Excel 读取所有有下载链接的行"""
    wb = openpyxl.load_workbook(EXCEL_FILE)
    ws = wb.active

    headers = [cell.value for cell in ws[1]]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=False):
        data = {}
        for i, cell in enumerate(row):
            if i < len(headers) and headers[i]:
                # 超链接用 hyperlink，否则用 value
                if cell.hyperlink:
                    data[headers[i]] = cell.hyperlink.target
                else:
                    data[headers[i]] = cell.value
        url = data.get("下载链接", "") or ""
        if url and url.startswith("http") and "annas-archive" in url:
            rows.append({
                "序号":    data.get("序号", ""),
                "书名":    data.get("原书名", "") or data.get("找到书名", ""),
                "url":     url,
            })
    return rows


def load_done() -> set:
    done = set()
    if Path(DONE_FILE).exists():
        with open(DONE_FILE, encoding="utf-8") as f:
            for line in f:
                done.add(line.strip())
    return done


def mark_done(num):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(str(num) + "\n")


def safe_filename(name: str) -> str:
    """把书名转成合法文件名"""
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    return name[:80].strip()


async def try_download(page, context, book_url: str, title: str) -> bool:
    """
    访问 Anna's Archive 详情页，尝试找到并点击下载链接。
    返回 True 表示成功触发下载。
    """
    try:
        await page.goto(book_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(1.0, 2.0))

        # ── 方案A：找 /slow_download/ 链接（Anna's Archive 自有慢速下载）──
        slow_links = await page.query_selector_all("a[href*='/slow_download/']")
        if slow_links:
            href = await slow_links[0].get_attribute("href")
            if href:
                dl_url = ("https://annas-archive.gl" + href
                          if href.startswith("/") else href)
                print(f"    → 慢速下载: {dl_url[:70]}")
                # 监听下载事件
                async with page.expect_download(timeout=60000) as dl_info:
                    await page.goto(dl_url, wait_until="domcontentloaded",
                                    timeout=30000)
                dl = await dl_info.value
                save_path = DOWNLOAD_DIR / f"{safe_filename(title)}.{dl.suggested_filename.split('.')[-1]}"
                await dl.save_as(save_path)
                print(f"    ✅ 已保存: {save_path.name}")
                return True

        # ── 方案B：找镜像链接（libgen / library.lol 等）──────────────────
        mirror_selectors = [
            "a[href*='library.lol']",
            "a[href*='libgen.']",
            "a[href*='libgen.rs']",
            "a[href*='libgen.is']",
            "a[href*='b-ok.']",
        ]
        for sel in mirror_selectors:
            els = await page.query_selector_all(sel)
            if els:
                href = await els[0].get_attribute("href") or ""
                if not href:
                    continue
                print(f"    → 镜像站: {href[:70]}")
                # 在新标签页打开镜像
                new_page = await context.new_page()
                try:
                    await new_page.goto(href, wait_until="domcontentloaded",
                                        timeout=30000)
                    await asyncio.sleep(1.5)
                    # libgen/library.lol 通常有 GET 或 download 按钮
                    for btn_sel in [
                        "a#download", "a[href*='get.php']",
                        "a[href*='/get/']", "a.btn-primary",
                        "a[href*='download']",
                    ]:
                        btns = await new_page.query_selector_all(btn_sel)
                        if btns:
                            async with new_page.expect_download(
                                    timeout=60000) as dl_info:
                                await btns[0].click()
                            dl = await dl_info.value
                            ext = dl.suggested_filename.split(".")[-1]
                            save_path = DOWNLOAD_DIR / f"{safe_filename(title)}.{ext}"
                            await dl.save_as(save_path)
                            print(f"    ✅ 已保存: {save_path.name}")
                            await new_page.close()
                            return True
                except Exception as e:
                    print(f"    ⚠ 镜像站出错: {e}")
                finally:
                    if not new_page.is_closed():
                        await new_page.close()

        print(f"    ❌ 未找到可用下载链接")
        return False

    except PlaywrightTimeout:
        print(f"    ⏱ 超时")
        return False
    except Exception as e:
        print(f"    ⚠ 出错: {e}")
        return False


async def main():
    DOWNLOAD_DIR.mkdir(exist_ok=True)

    books = load_links()
    done  = load_done()

    pending = [b for b in books if str(b["序号"]) not in done]
    print(f"📚 共 {len(books)} 条链接 | 已下载 {len(done)} 本 | 待下载 {len(pending)} 本")
    print(f"📁 保存到: {DOWNLOAD_DIR.absolute()}\n")

    if not pending:
        print("✅ 全部已下载！")
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
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 800},
            locale="zh-CN",
            accept_downloads=True,
        )
        page = await context.new_page()

        success = 0
        fail    = 0

        for i, book in enumerate(pending):
            num   = book["序号"]
            title = book["书名"] or f"书_{num}"
            url   = book["url"]

            print(f"[{i+1:4d}/{len(pending)}] #{num} {title[:45]}")

            ok = await try_download(page, context, url, title)
            if ok:
                success += 1
                mark_done(num)
            else:
                fail += 1

            await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        await browser.close()

    print(f"\n🎉 完成！成功 {success} 本 | 失败 {fail} 本")
    print(f"📁 文件保存在: {DOWNLOAD_DIR.absolute()}")


if __name__ == "__main__":
    asyncio.run(main())
