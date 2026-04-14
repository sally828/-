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
        await asyncio.sleep(random.uniform(1.5, 2.5))

        # ── 调试：收集页面上所有链接，找下载相关的 ──────────────────────
        all_links = await page.query_selector_all("a[href]")
        download_candidates = []
        for link in all_links:
            href = await link.get_attribute("href") or ""
            text = (await link.inner_text()).strip()[:30]
            # 收集所有看起来像下载链接的
            if any(kw in href for kw in [
                "slow_download", "fast_download", "download",
                "libgen", "library.lol", "b-ok", "zlibrary",
                "z-lib", "1lib", "books.ms", "ipfs",
            ]):
                download_candidates.append((href, text))

        if download_candidates:
            print(f"    找到 {len(download_candidates)} 个候选链接")
        else:
            # 打印页面所有链接供调试（仅前10个）
            print(f"    ⚠ 未找到下载候选，页面链接样本：")
            for link in all_links[:10]:
                href = await link.get_attribute("href") or ""
                if href and not href.startswith("#"):
                    print(f"       {href[:80]}")
            return False

        # ── 方案A：优先找 slow_download（Anna's Archive 自有）────────────
        slow = [(h, t) for h, t in download_candidates if "slow_download" in h]
        if slow:
            href = slow[0][0]
            dl_url = "https://annas-archive.gl" + href if href.startswith("/") else href
            print(f"    → 慢速下载: {dl_url[:70]}")
            try:
                async with page.expect_download(timeout=90000) as dl_info:
                    await page.goto(dl_url, wait_until="domcontentloaded", timeout=40000)
                dl = await dl_info.value
                ext = dl.suggested_filename.split(".")[-1] or "pdf"
                save_path = DOWNLOAD_DIR / f"{safe_filename(title)}.{ext}"
                await dl.save_as(save_path)
                print(f"    ✅ 已保存: {save_path.name}")
                return True
            except Exception as e:
                print(f"    ⚠ 慢速下载失败: {e}，尝试镜像...")

        # ── 方案B：镜像站（library.lol / libgen 等）─────────────────────
        mirrors = [(h, t) for h, t in download_candidates
                   if any(m in h for m in ["library.lol", "libgen", "b-ok", "z-lib", "books.ms"])]
        for href, _ in mirrors[:3]:   # 最多试3个镜像
            print(f"    → 镜像站: {href[:70]}")
            new_page = await context.new_page()
            try:
                await new_page.goto(href, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)

                # 在镜像站找下载按钮
                for btn_sel in [
                    "a#download", "a[href*='get.php']", "a[href*='/get/']",
                    "a.btn-success", "a.btn-primary", "input[value='GET']",
                    "a[href*='download']", "a[href*='.pdf']", "a[href*='.epub']",
                ]:
                    btns = await new_page.query_selector_all(btn_sel)
                    if btns:
                        try:
                            async with new_page.expect_download(timeout=60000) as dl_info:
                                await btns[0].click()
                            dl = await dl_info.value
                            ext = dl.suggested_filename.split(".")[-1] or "pdf"
                            save_path = DOWNLOAD_DIR / f"{safe_filename(title)}.{ext}"
                            await dl.save_as(save_path)
                            print(f"    ✅ 已保存: {save_path.name}")
                            await new_page.close()
                            return True
                        except Exception:
                            continue
            except Exception as e:
                print(f"    ⚠ 镜像站失败: {e}")
            finally:
                if not new_page.is_closed():
                    await new_page.close()

        # ── 方案C：其他 download 链接 ────────────────────────────────────
        others = [(h, t) for h, t in download_candidates
                  if "download" in h and h not in [x[0] for x in slow + mirrors]]
        for href, _ in others[:2]:
            print(f"    → 尝试: {href[:70]}")
            try:
                async with page.expect_download(timeout=60000) as dl_info:
                    await page.goto(href, wait_until="domcontentloaded", timeout=30000)
                dl = await dl_info.value
                ext = dl.suggested_filename.split(".")[-1] or "pdf"
                save_path = DOWNLOAD_DIR / f"{safe_filename(title)}.{ext}"
                await dl.save_as(save_path)
                print(f"    ✅ 已保存: {save_path.name}")
                return True
            except Exception:
                continue

        print(f"    ❌ 所有方案均失败")
        return False

    except PlaywrightTimeout:
        print(f"    ⏱ 超时")
        return False
    except Exception as e:
        print(f"    ⚠ 出错: {e}")


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
