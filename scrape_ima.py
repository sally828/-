#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本
已知 API 端点和根目录 ID，直接调用递归提取完整书单
"""

import asyncio
import csv
import json
import re
from collections import Counter
from pathlib import Path

from playwright.async_api import async_playwright

# ── 配置 ──────────────────────────────────────────────────────────────────────
KB_NAME    = "追梦人的财经图书馆"
SHARE_URL  = "https://ima.qq.com/wiki/?shareId=80155f2d7bdbe7249ae15f61cb22c37f997fb4dc0f8f60dfd70185a94e4905f5"
SHARE_ID   = "80155f2d7bdbe7249ae15f61cb22c37f997fb4dc0f8f60dfd70185a94e4905f5"
ROOT_ID    = "7374371035301653"
API_URL    = "https://ima.qq.com/cgi-bin/knowledge_share_get/get_share_info"
PROXY      = "http://127.0.0.1:10808"
CDP_URL    = "http://localhost:9222"
OUTPUT_CSV = Path(__file__).parent / "ima_booklist.csv"
DEBUG_JSON = Path(__file__).parent / "ima_debug.json"
# ─────────────────────────────────────────────────────────────────────────────


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def is_book_name(name: str) -> bool:
    name = name.strip()
    return len(name) >= 3 and not name.startswith("http")


async def api_call(page, body: dict) -> dict:
    """在浏览器 cookies 上下文中 POST 调用 get_share_info"""
    return await page.evaluate("""
        async (args) => {
            try {
                const r = await fetch(args.url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify(args.body)
                });
                return await r.json();
            } catch(e) { return {code: -1, error: e.toString()}; }
        }
    """, {"url": API_URL, "body": body}) or {}


async def find_working_template(page) -> dict | None:
    """
    探测哪种 body 格式能让 knowledge_list 返回真实内容。
    候选格式从最简到最完整依次尝试。
    """
    candidates = [
        {"folder_id": ROOT_ID, "cursor": "", "limit": 50},
        {"share_id": SHARE_ID, "folder_id": ROOT_ID, "cursor": "", "limit": 50},
        {"folder_id": ROOT_ID, "cursor": ""},
        {"folder_id": ROOT_ID, "cursor": "", "count": 50},
        {"knowledge_base_id": ROOT_ID, "folder_id": ROOT_ID, "cursor": "", "limit": 50},
        {"share_id": SHARE_ID, "cursor": "", "limit": 50},
    ]

    debug_log = []
    for body in candidates:
        result = await api_call(page, body)
        code  = result.get("code", -1)
        items = result.get("knowledge_list", [])
        entry = {
            "body": body,
            "code": code,
            "items": len(items),
            "is_end": result.get("is_end"),
            "next_cursor": result.get("next_cursor", "")[:40],
            "sample": items[:2] if items else [],
        }
        debug_log.append(entry)
        print(f"  {json.dumps(body, ensure_ascii=False)[:75]}")
        print(f"  => code={code}, {len(items)} 条, is_end={result.get('is_end')}, cursor={result.get('next_cursor','')[:20]}")

        if code == 0 and items:
            print("  ✅ 有效格式！")
            DEBUG_JSON.write_text(json.dumps(debug_log, ensure_ascii=False, indent=2), encoding="utf-8")
            # 返回去掉 folder_id / cursor 的模板（fetch_folder 会动态填入）
            return {k: v for k, v in body.items() if k not in ("folder_id", "cursor")}

        await asyncio.sleep(0.4)

    DEBUG_JSON.write_text(json.dumps(debug_log, ensure_ascii=False, indent=2), encoding="utf-8")
    return None


async def fetch_folder(page, tmpl: dict, folder_id: str,
                       folder_name: str, depth: int = 0) -> list:
    books, cursor, indent = [], "", "  " * depth
    print(f"{indent}📁 {folder_name}")
    while True:
        body = {**tmpl, "folder_id": folder_id, "cursor": cursor}
        data = await api_call(page, body)

        if data.get("code") != 0:
            print(f"{indent}  ⚠ code={data.get('code')}: {data.get('msg', data.get('error', ''))}")
            break

        items = data.get("knowledge_list", [])
        for item in items:
            if not isinstance(item, dict):
                continue
            name  = clean(item.get("name") or item.get("title") or "")
            itype = item.get("type")
            if itype in (2, "folder", "dir", "directory"):
                fid = item.get("folder_id") or item.get("id") or ""
                if fid and name:
                    books.extend(await fetch_folder(page, tmpl, fid, name, depth + 1))
            elif is_book_name(name):
                books.append({"书名": name, "分类": folder_name})

        next_cur = data.get("next_cursor", "")
        if data.get("is_end", True) or not items or not next_cur or next_cur == cursor:
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
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            ctx  = browser.contexts[0] if browser.contexts else None
            page = (ctx.pages[0] if (ctx and ctx.pages)
                    else await ctx.new_page() if ctx
                    else await browser.new_page())
            using_cdp = True
            print(f"✅ 已连接到 360浏览器（CDP），当前页面：{page.url}")
        except Exception:
            print("未检测到 360浏览器，使用内置 Chromium…")
            browser = await pw.chromium.launch(headless=False, args=browser_args)
            ctx  = await browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"))
            page = await ctx.new_page()

        # 导航到分享链接，建立含 share_id 的 cookie 上下文
        if SHARE_URL not in page.url:
            print("\n正在打开分享链接（请确保已登录腾讯账号）…")
            await page.goto(SHARE_URL, wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(4)
        else:
            await asyncio.sleep(2)
        print(f"当前页面：{page.url}")

        # 验证 API 连通性
        print("\n验证 API 可访问性…")
        init = await api_call(page, {})
        if init.get("code") != 0:
            print(f"⚠ API 不可用，code={init.get('code')}，请确认已登录腾讯账号")
            if not using_cdp:
                await browser.close()
            return
        kb_name = (init.get("knowledge_base_info") or {}).get("basic_info", {}).get("name", "")
        total   = init.get("total_size", "?")
        print(f"✅ API 正常，知识库：{kb_name}，总文件数：{total}")

        # 探测有效请求体格式
        print("\n正在探测 API 请求体格式（尝试多种参数组合）…")
        tmpl = await find_working_template(page)

        if tmpl is None:
            print(f"\n⚠ 所有格式均未返回数据，请把 {DEBUG_JSON.name} 发给我分析。")
            if not using_cdp:
                await browser.close()
            return

        print(f"\n开始递归提取书单（参数模板：{tmpl}）…\n")
        books = await fetch_folder(page, tmpl, ROOT_ID, KB_NAME, 0)

        if not using_cdp:
            await browser.close()

    if not books:
        print(f"\n⚠ 未能提取到书目，请把 {DEBUG_JSON.name} 发给我。")
        return

    books.sort(key=lambda b: (b["分类"], b["书名"]))
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["序号", "分类", "原书名", "状态", "找到书名"])
        writer.writeheader()
        for i, b in enumerate(books, 1):
            writer.writerow({"序号": i, "分类": b["分类"], "原书名": b["书名"],
                             "状态": "", "找到书名": ""})

    print(f"\n✅ 书单已保存：{OUTPUT_CSV}，共 {len(books)} 条\n")
    counts = Counter(b["分类"] for b in books)
    for cat, cnt in sorted(counts.items()):
        print(f"   {cat}：{cnt} 本")


if __name__ == "__main__":
    asyncio.run(main())
