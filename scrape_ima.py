#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本（直接 API 版）
登录后直接调用 IMA 后台 API，递归获取所有文件夹和书目，无需点击任何按钮
"""

import asyncio
import csv
import json
import re
from collections import Counter
from pathlib import Path

from playwright.async_api import async_playwright

# ── 配置 ──────────────────────────────────────────────────────────────────────
IMA_URL   = "https://ima.qq.com/wiki/?shareId=80155f2d7bdbe7249ae15f61cb22c37f997fb4dc0f8f60dfd70185a94e4905f5"
SHARE_ID  = "80155f2d7bdbe7249ae15f61cb22c37f997fb4dc0f8f60dfd70185a94e4905f5"
ROOT_ID   = "7374371035301653"
PROXY     = "http://127.0.0.1:10808"
CDP_URL   = "http://localhost:9222"
OUTPUT_CSV = Path(__file__).parent / "ima_booklist.csv"
DEBUG_JSON = Path(__file__).parent / "ima_raw.json"
# ─────────────────────────────────────────────────────────────────────────────


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def is_book_name(name: str) -> bool:
    name = name.strip()
    return len(name) >= 3 and not name.startswith("http")


async def call_api(page, folder_id: str, cursor: str = "", count: int = 50) -> dict:
    """用浏览器的登录态直接调用 IMA API"""
    return await page.evaluate("""
        async (args) => {
            try {
                const resp = await fetch('/cgi-bin/knowledge_share_get/get_share_info', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify({
                        share_id: args.shareId,
                        folder_id: args.folderId,
                        cursor: args.cursor,
                        count: args.count
                    })
                });
                return await resp.json();
            } catch(e) {
                return {code: -1, error: e.toString()};
            }
        }
    """, {"shareId": SHARE_ID, "folderId": folder_id,
          "cursor": cursor, "count": count}) or {}


async def fetch_folder(page, folder_id: str, folder_name: str,
                       depth: int = 0, debug_log: list = None) -> list[dict]:
    """递归获取文件夹内所有书目"""
    books = []
    cursor = ""
    indent = "  " * depth

    while True:
        data = await call_api(page, folder_id, cursor)

        if debug_log is not None:
            debug_log.append({"folder": folder_name, "cursor": cursor, "resp": data})

        code = data.get("code", -1)
        if code != 0:
            print(f"{indent}  ⚠ API 错误 code={code}: {data.get('error', data.get('msg', ''))}")
            break

        items = data.get("knowledge_list", [])

        for item in items:
            if not isinstance(item, dict):
                continue
            name = clean(item.get("name") or item.get("title") or "")
            itype = item.get("type")

            # 文件夹：递归
            if itype in (2, "folder", "dir", "directory"):
                fid = item.get("folder_id") or item.get("id") or ""
                if fid and name:
                    print(f"{indent}  📁 {name}")
                    sub = await fetch_folder(page, fid, name, depth + 1, debug_log)
                    books.extend(sub)
            else:
                # 文件/书目
                if is_book_name(name):
                    books.append({"书名": name, "分类": folder_name})

        is_end = data.get("is_end", True)
        next_cur = data.get("next_cursor", "")

        if is_end or not items or not next_cur or next_cur == cursor:
            break

        cursor = next_cur
        await asyncio.sleep(0.3)

    return books


async def main():
    browser_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    if PROXY:
        browser_args.append(f"--proxy-server={PROXY}")

    async with async_playwright() as pw:
        using_cdp = False
        page = None

        # ── 优先连接已有的 360 浏览器 ────────────────────────────────────────
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            ctx = browser.contexts[0] if browser.contexts else None
            page = (ctx.pages[0] if (ctx and ctx.pages)
                    else await ctx.new_page() if ctx
                    else await browser.new_page())
            using_cdp = True
            print("✅ 已连接到 360浏览器（CDP）")
        except Exception:
            print("未检测到 360浏览器，使用内置 Chromium…")
            browser = await pw.chromium.launch(headless=False, args=browser_args)
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
                )
            )
            page = await ctx.new_page()

        # ── 登录（仅内置 Chromium 需要）──────────────────────────────────────
        if not using_cdp:
            print("\n正在打开 IMA 登录页…")
            await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40000)
            print("\n请在浏览器里登录腾讯账号，完成后按 Enter")
            input(">>> ")

        # ── 跳转到书单页面（触发 auth cookie） ───────────────────────────────
        print("\n正在跳转到书单页面…")
        await page.goto(IMA_URL, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(3)

        # ── 直接调用 API 提取全部书目 ─────────────────────────────────────────
        print("\n开始通过 API 提取书单（无需手动操作）…\n")
        debug_log: list = []
        books = await fetch_folder(page, ROOT_ID, "根目录", 0, debug_log)

        # 保存调试日志（最多 50 条）
        DEBUG_JSON.write_text(
            json.dumps(debug_log[:50], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        if not using_cdp:
            await browser.close()

    # ── 输出结果 ──────────────────────────────────────────────────────────────
    if not books:
        print("\n⚠  未能提取到书目。")
        print(f"请把 {DEBUG_JSON.name} 发给我，我来分析 API 结构。")
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

    print(f"\n✅ 书单已保存：{OUTPUT_CSV}，共 {len(books)} 条\n")
    counts = Counter(b["分类"] for b in books)
    for cat, cnt in sorted(counts.items()):
        print(f"   {cat}：{cnt} 本")

    print(f"\n下一步：把 {OUTPUT_CSV.name} 改名为 books_input.csv，再运行 search_anna.py")


if __name__ == "__main__":
    asyncio.run(main())
