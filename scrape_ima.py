#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本
同时拦截 请求体 + 响应，从真实参数里还原 API 调用方式，递归提取完整书单
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
PROXY     = "http://127.0.0.1:10808"
CDP_URL   = "http://localhost:9222"
OUTPUT_CSV = Path(__file__).parent / "ima_booklist.csv"
DEBUG_REQ  = Path(__file__).parent / "ima_requests.json"   # 请求体日志
DEBUG_RESP = Path(__file__).parent / "ima_raw.json"         # 响应日志
# ─────────────────────────────────────────────────────────────────────────────


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def is_book_name(name: str) -> bool:
    name = name.strip()
    return len(name) >= 3 and not name.startswith("http")


async def call_api(page, url: str, body: dict) -> dict:
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
    """, {"url": url, "body": body}) or {}


async def fetch_folder(page, api_url: str, base_body: dict,
                       folder_id: str, folder_name: str, depth: int = 0) -> list:
    books, cursor, indent = [], "", "  " * depth
    while True:
        body = {**base_body, "folder_id": folder_id, "cursor": cursor}
        data = await call_api(page, api_url, body)

        if data.get("code") != 0:
            print(f"{indent}  ⚠ code={data.get('code')}: {data.get('msg', data.get('error', ''))}")
            break

        for item in data.get("knowledge_list", []):
            if not isinstance(item, dict):
                continue
            name = clean(item.get("name") or item.get("title") or "")
            itype = item.get("type")
            if itype in (2, "folder", "dir", "directory"):
                fid = item.get("folder_id") or item.get("id") or ""
                if fid and name:
                    print(f"{indent}  📁 {name}")
                    books.extend(await fetch_folder(page, api_url, base_body,
                                                    fid, name, depth + 1))
            elif is_book_name(name):
                books.append({"书名": name, "分类": folder_name})

        next_cur = data.get("next_cursor", "")
        if data.get("is_end", True) or not data.get("knowledge_list") or \
                not next_cur or next_cur == cursor:
            break
        cursor = next_cur
        await asyncio.sleep(0.3)
    return books


async def main():
    browser_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    if PROXY:
        browser_args.append(f"--proxy-server={PROXY}")

    req_log, resp_log = [], []

    async def on_request(request):
        if "ima.qq.com" not in request.url:
            return
        try:
            body = json.loads(request.post_data or "{}")
        except Exception:
            body = {}
        req_log.append({"url": request.url, "body": body})

    async def on_response(response):
        if "ima.qq.com" not in response.url:
            return
        try:
            if "json" not in response.headers.get("content-type", ""):
                return
            body = await response.json()
            resp_log.append({"url": response.url, "body": body})
        except Exception:
            pass

    async with async_playwright() as pw:
        using_cdp = False
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
            ctx = await browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"))
            page = await ctx.new_page()

        if not using_cdp:
            await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40000)
            print("请登录后按 Enter")
            input(">>> ")

        # 同时拦截请求体和响应
        page.on("request", on_request)
        page.on("response", on_response)

        # 导航到分享链接，让页面自己调用 get_share_info
        print("\n正在导航到书单分享页，捕获真实 API 参数…")
        await page.goto(IMA_URL, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(5)

        # 保存调试数据
        DEBUG_REQ.write_text(json.dumps(req_log[:20], ensure_ascii=False, indent=2), encoding="utf-8")
        DEBUG_RESP.write_text(json.dumps(resp_log[:20], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"拦截到 {len(req_log)} 个请求，{len(resp_log)} 个响应")

        # 从拦截的请求里找 get_share_info 的真实参数
        api_url, base_body, root_id = None, {}, ""
        for r in req_log:
            if "get_share_info" in r["url"] or "knowledge" in r["url"]:
                api_url = r["url"]
                base_body = {k: v for k, v in r["body"].items()
                             if k not in ("folder_id", "cursor")}
                print(f"  找到 API：{api_url}")
                print(f"  参数：{base_body}")
                break

        # 从响应里找根文件夹 ID
        for r in resp_log:
            body = r["body"]
            if not isinstance(body, dict) or body.get("code") != 0:
                continue
            path = body.get("current_path", [])
            if path:
                root_id = path[-1].get("folder_id", "")
                if root_id:
                    print(f"  根文件夹 ID：{root_id}")
                    if not api_url:
                        api_url = r["url"]
                    break

        if not api_url or not root_id:
            print("\n⚠ 未能自动捕获 API 参数。")
            print(f"请把 ima_requests.json 和 ima_raw.json 都发给我。")
            if not using_cdp:
                await browser.close()
            return

        # 用捕获到的真实参数递归提取
        print("\n开始全自动提取书单…\n")
        books = await fetch_folder(page, api_url, base_body,
                                   root_id, "追梦人的财经图书馆", 0)

        if not using_cdp:
            await browser.close()

    if not books:
        print("\n⚠  未能提取到书目，请把 ima_requests.json 和 ima_raw.json 发给我。")
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
