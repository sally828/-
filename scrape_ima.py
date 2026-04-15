#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本（全自动版）
推荐用 start_360.bat 启动：自动用 360极速浏览器登录，脚本连上去全自动提取。
也可以直接运行：python scrape_ima.py（会用内置 Chromium）
"""

import asyncio
import csv
import json
import re
from collections import Counter
from pathlib import Path

from playwright.async_api import async_playwright

# ── 配置 ──────────────────────────────────────────────────────────────────────
IMA_URL    = "https://ima.qq.com/wiki/?shareId=80155f2d7bdbe7249ae15f61cb22c37f997fb4dc0f8f60dfd70185a94e4905f5"
PROXY      = "http://127.0.0.1:10808"   # 不需要代理改成 None
CDP_URL    = "http://localhost:9222"     # start_360.bat 启动的调试端口
OUTPUT_CSV = Path(__file__).parent / "ima_booklist.csv"
DEBUG_JSON = Path(__file__).parent / "ima_raw.json"
# ─────────────────────────────────────────────────────────────────────────────


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def is_book_name(name: str) -> bool:
    name = name.strip()
    return len(name) >= 3 and not name.startswith("http")


async def auto_expand_tree(page) -> None:
    """
    全自动展开左侧文件夹树，触发所有 API 数据加载。
    只操作左侧面板（x 坐标 < 500px），不误点正文区域。
    """
    print("  自动展开文件夹树中，请勿操作浏览器…")
    done_keys: set[str] = set()

    for round_num in range(50):
        new_clicks = 0

        # ① aria-expanded="false" —— 最可靠的树节点标记
        try:
            for el in await page.query_selector_all('[aria-expanded="false"]'):
                bb = await el.bounding_box()
                if not bb or bb["x"] > 500:
                    continue
                key = f"{bb['x']:.0f},{bb['y']:.0f}"
                if key in done_keys:
                    continue
                try:
                    await el.click(timeout=2000)
                    await asyncio.sleep(0.5)
                    new_clicks += 1
                except Exception:
                    pass
                done_keys.add(key)
        except Exception:
            pass

        # ② class 含展开箭头关键词的元素
        for kw in ["arrow", "Arrow", "toggle", "Toggle",
                   "chevron", "Chevron", "expand", "Expand",
                   "collapse", "Fold", "fold", "caret"]:
            try:
                for el in await page.query_selector_all(f'[class*="{kw}"]'):
                    bb = await el.bounding_box()
                    if not bb or bb["width"] < 4 or bb["x"] > 500:
                        continue
                    key = f"{bb['x']:.0f},{bb['y']:.0f}"
                    if key in done_keys:
                        continue
                    try:
                        await el.click(timeout=2000)
                        await asyncio.sleep(0.5)
                        new_clicks += 1
                    except Exception:
                        pass
                    done_keys.add(key)
            except Exception:
                pass

        # ③ 滚动触发懒加载
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.0)

        if new_clicks > 0:
            print(f"    第 {round_num + 1} 轮：新展开 {new_clicks} 个节点")
        else:
            print(f"  ✅ 展开完成，共操作 {len(done_keys)} 次")
            break

    # 最后等 2 秒，让 API 响应全部到达
    await asyncio.sleep(2)


async def main():
    raw_api: list[dict] = []

    async def on_response(response):
        url = response.url
        if "ima.qq.com" not in url and "imaqq" not in url:
            return
        try:
            if "json" not in response.headers.get("content-type", ""):
                return
            body = await response.json()
            raw_api.append({"url": url, "body": body})
        except Exception:
            pass

    args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    if PROXY:
        args.append(f"--proxy-server={PROXY}")

    async with async_playwright() as pw:

        # ── 优先连接 start_360.bat 启动的 360浏览器 ──────────────────────────
        using_cdp = False
        browser = None
        page = None

        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            ctx = browser.contexts[0] if browser.contexts else None
            if ctx and ctx.pages:
                page = ctx.pages[0]
            elif ctx:
                page = await ctx.new_page()
            else:
                page = await browser.new_page()
            using_cdp = True
            print("✅ 已连接到 360极速浏览器（CDP）")
        except Exception:
            print("未检测到 360极速浏览器，改用内置 Chromium…")
            browser = await pw.chromium.launch(headless=False, args=args)
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
                ),
            )
            page = await ctx.new_page()

        page.on("response", on_response)

        # ── 登录（只在用内置 Chromium 时才需要手动登录）────────────────────────
        if not using_cdp:
            print("\n正在打开 IMA 登录页…")
            await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40000)
            print()
            print("=" * 58)
            print(" 请在浏览器里登录腾讯账号")
            print(" 登录完成后回到这里按 Enter，其余全自动")
            print("=" * 58)
            input(">>> 登录完成后按 Enter：")
        else:
            print("（360浏览器已登录，跳过登录步骤）")

        # ── 跳转书单页面 ──────────────────────────────────────────────────────
        print("\n正在跳转到书单页面…")
        await page.goto(IMA_URL, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(4)

        # ── 全自动展开所有文件夹 ──────────────────────────────────────────────
        print("\n开始全自动提取（无需任何操作）…")
        await auto_expand_tree(page)

        await page.screenshot(path=str(Path(__file__).parent / "ima_debug.png"))

        if not using_cdp:
            await browser.close()
        # CDP 模式不关闭浏览器，保留 360 窗口

    # ── 解析拦截到的 API 数据 ────────────────────────────────────────────────
    print(f"\n拦截到 {len(raw_api)} 个 API 响应，开始解析…")
    DEBUG_JSON.write_text(
        json.dumps(raw_api[:300], ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    books: list[dict] = []
    seen: set = set()
    current_folder = "未分类"

    def add_book(name: str, category: str):
        name = clean(name)
        if not is_book_name(name):
            return
        key = (name, category)
        if key not in seen:
            seen.add(key)
            books.append({"书名": name, "分类": category})

    for r in raw_api:
        body = r["body"]
        data = body if isinstance(body, dict) else {}
        for key in ("data", "result", "list", "items", "files", "nodes", "children"):
            items = data.get(key) or (data.get("data") or {}).get(key, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") in ("folder", "dir", "directory", 2):
                    fn = clean(item.get("name") or item.get("title") or "")
                    if fn:
                        current_folder = fn
                name = (
                    item.get("name") or item.get("title") or
                    item.get("fileName") or item.get("file_name") or ""
                )
                if name:
                    add_book(name, current_folder)

    if not books:
        print("\n⚠  未能提取到书目。请把 ima_raw.json 发给我分析 API 结构。")
        return

    books.sort(key=lambda b: (b["分类"], b["书名"]))

    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["序号", "分类", "原书名", "状态", "找到书名"])
        writer.writeheader()
        for i, b in enumerate(books, 1):
            writer.writerow({
                "序号":    i,
                "分类":    b["分类"],
                "原书名":  b["书名"],
                "状态":    "",
                "找到书名": "",
            })

    print(f"\n✅ 书单已保存：{OUTPUT_CSV}")
    print(f"   共 {len(books)} 条\n")
    counts = Counter(b["分类"] for b in books)
    for cat, cnt in sorted(counts.items()):
        print(f"   {cat}：{cnt} 本")

    print(f"\n下一步：把 {OUTPUT_CSV.name} 改名为 books_input.csv，再运行 search_anna.py")


if __name__ == "__main__":
    asyncio.run(main())
