#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本
从「追梦人的财经图书馆」提取书单，保留文件夹分类结构
输出格式与 books_input.csv 兼容，直接供 search_anna.py 使用

运行：python scrape_ima.py
不需要登录，直接打开分享链接即可
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
OUTPUT_CSV = Path(__file__).parent / "ima_booklist.csv"
DEBUG_JSON = Path(__file__).parent / "ima_raw.json"
# ─────────────────────────────────────────────────────────────────────────────


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def is_book_name(name: str) -> bool:
    """过滤掉太短或明显不是书名的条目"""
    name = name.strip()
    return len(name) >= 3 and not name.startswith("http")


async def scroll_load(page, max_rounds: int = 30):
    """滚动到底部直到内容不再增加"""
    prev = -1
    for _ in range(max_rounds):
        h = await page.evaluate("document.body.scrollHeight")
        if h == prev:
            break
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.0)
        prev = h


async def main():
    raw_api: list[dict] = []       # 拦截到的 API 响应
    books:   list[dict] = []       # 提取结果

    async def on_response(response):
        url = response.url
        # IMA 的 API 在 ima.qq.com 域下
        if "ima.qq.com" not in url and "imaqq" not in url:
            return
        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            body = await response.json()
            raw_api.append({"url": url, "body": body})
        except Exception:
            pass

    args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    if PROXY:
        args.append(f"--proxy-server={PROXY}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, args=args)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        page.on("response", on_response)

        print(f"\n正在打开 IMA 知识库…")
        await page.goto(IMA_URL, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(3)

        print()
        print("=" * 58)
        print(" IMA 书单提取 — 操作说明")
        print("=" * 58)
        print(" 页面打开后，依次点击左侧每个文件夹：")
        print("   01商业认知与决策体系")
        print("   06营销学")
        print("   10商业模式  ……等等")
        print()
        print(" 每进入一个文件夹：")
        print("   → 等右侧文件列表加载完")
        print("   → 滚动到底部（确保全部加载）")
        print("   → 再点击下一个文件夹")
        print()
        print(" 全部文件夹都点完后，回到这里按 Enter")
        print("=" * 58)
        input(">>> 全部处理完后按 Enter：")

        # 截图方便调试
        await page.screenshot(path=str(Path(__file__).parent / "ima_debug.png"))

        # ── 方法一：解析 API 拦截数据 ────────────────────────────────────────
        print(f"\n拦截到 {len(raw_api)} 个 API 响应，开始解析…")
        DEBUG_JSON.write_text(
            json.dumps(raw_api[:200], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        seen = set()

        def add_book(name: str, category: str):
            name = clean(name)
            if not is_book_name(name):
                return
            key = (name, category)
            if key not in seen:
                seen.add(key)
                books.append({"书名": name, "分类": category})

        # 解析 API 响应（IMA 的具体字段需要根据实际响应调整）
        current_folder = "未分类"
        for r in raw_api:
            body = r["body"]
            url  = r["url"]

            # 尝试常见结构
            data = body if isinstance(body, dict) else {}

            # 从各种可能的字段里找文件列表
            for key in ("data", "result", "list", "items", "files", "nodes", "children"):
                items = data.get(key) or (data.get("data") or {}).get(key, [])
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    # 文件夹名
                    if item.get("type") in ("folder", "dir", "directory", 2):
                        folder_name = clean(item.get("name") or item.get("title") or "")
                        if folder_name:
                            current_folder = folder_name
                    # 文件名
                    name = (
                        item.get("name") or
                        item.get("title") or
                        item.get("fileName") or
                        item.get("file_name") or
                        ""
                    )
                    if name:
                        add_book(name, current_folder)

        # ── 方法二：直接解析 DOM（API 解析失败时的备用）────────────────────
        if not books:
            print("API 解析未获取到数据，尝试从页面 DOM 提取…")
            try:
                dom_data = await page.evaluate("""
                    () => {
                        const result = [];
                        // 找所有可能的文件名元素
                        const selectors = [
                            '[class*="file-name"]', '[class*="fileName"]',
                            '[class*="item-name"]', '[class*="itemName"]',
                            '[class*="doc-title"]', '[class*="docTitle"]',
                            '[class*="node-title"]', '[class*="nodeTitle"]',
                            '.file-item span', '.tree-node span',
                        ];
                        for (const sel of selectors) {
                            const els = document.querySelectorAll(sel);
                            if (els.length > 2) {
                                els.forEach(el => {
                                    const t = el.textContent.trim();
                                    if (t.length >= 3) result.push(t);
                                });
                            }
                        }
                        return [...new Set(result)];
                    }
                """)
                for name in dom_data or []:
                    add_book(name, "未分类（DOM提取）")
            except Exception as e:
                print(f"DOM 提取失败：{e}")

        await browser.close()

    # ── 输出结果 ──────────────────────────────────────────────────────────────
    if not books:
        print("\n⚠  未能提取到书目。")
        print(f"请把 {DEBUG_JSON.name} 发给我，我来分析 API 结构并调整脚本。")
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

    print(f"\n下一步：把 {OUTPUT_CSV.name} 复制/改名为 books_input.csv，")
    print("再运行 search_anna.py 搜索下载链接。")


if __name__ == "__main__":
    asyncio.run(main())
