#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本
以成员身份登录，自动点击共享知识库，拦截真实 API 参数，递归提取完整书单
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
PROXY      = "http://127.0.0.1:10808"
CDP_URL    = "http://localhost:9222"
OUTPUT_CSV = Path(__file__).parent / "ima_booklist.csv"
DEBUG_REQ  = Path(__file__).parent / "ima_requests.json"
DEBUG_RESP = Path(__file__).parent / "ima_raw.json"
# ─────────────────────────────────────────────────────────────────────────────


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def is_book_name(name: str) -> bool:
    name = name.strip()
    return len(name) >= 3 and not name.startswith("http")


def is_knowledge_api(url: str) -> bool:
    """判断是否为知识库内容 API（排除静态资源）"""
    static_exts = ('.js', '.css', '.png', '.jpg', '.ico', '.woff', '.woff2', '.svg', '.map')
    if any(url.endswith(ext) or ('.' + ext.lstrip('.') + '?') in url for ext in static_exts):
        return False
    keywords = ('knowledge', 'folder', 'node', 'list', 'file')
    return any(k in url for k in keywords) and 'cgi-bin' in url


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

        items = data.get("knowledge_list", [])
        for item in items:
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
        if data.get("is_end", True) or not items or not next_cur or next_cur == cursor:
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
            print(f"✅ 已连接到 360浏览器（CDP），当前页面：{page.url}")
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

        # 注册拦截器
        page.on("request", on_request)
        page.on("response", on_response)

        # 停留在 ima.qq.com，不跳转到分享链接
        if not page.url.startswith("https://ima.qq.com"):
            print("正在导航到 IMA 主页…")
            await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(2)

        # 尝试自动点击左侧「追梦人的财经图书馆」
        print(f"\n正在查找并点击「{KB_NAME}」…")
        clicked = False
        for selector in [
            f'text="{KB_NAME}"',
            f':text("{KB_NAME}")',
            f'[title="{KB_NAME}"]',
            f'span:has-text("{KB_NAME}")',
            f'div:has-text("{KB_NAME}")',
        ]:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible(timeout=3000):
                    await locator.click(timeout=3000)
                    clicked = True
                    print(f"  ✅ 点击成功")
                    break
            except Exception:
                pass

        if not clicked:
            print(f"  未能自动点击，请手动点击左侧「{KB_NAME}」后按 Enter")
            input("  >>> ")

        print("等待 API 响应…")
        await asyncio.sleep(6)

        # 保存调试数据
        DEBUG_REQ.write_text(json.dumps(req_log, ensure_ascii=False, indent=2), encoding="utf-8")
        DEBUG_RESP.write_text(json.dumps(resp_log[:20], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"拦截到 {len(req_log)} 个请求，{len(resp_log)} 个响应")

        # 打印所有 knowledge 相关 API URL（排除静态文件）
        kb_requests = [r for r in req_log if is_knowledge_api(r["url"])]
        print(f"知识库 API 请求：{len(kb_requests)} 个")
        for r in kb_requests:
            print(f"  {r['url']}  参数：{r['body']}")

        # 从请求里找正确的 API
        api_url, base_body, root_id = None, {}, ""
        for r in kb_requests:
            body = r["body"]
            # 找包含 folder_id 或 knowledge_base_id 的请求
            if "folder_id" in body or "knowledge_base_id" in body:
                api_url = r["url"]
                base_body = {k: v for k, v in body.items()
                             if k not in ("folder_id", "cursor")}
                root_id = body.get("folder_id") or body.get("knowledge_base_id") or ""
                print(f"\n✅ 找到 API：{api_url}")
                print(f"   参数模板：{base_body}")
                print(f"   根 ID：{root_id}")
                break

        # 从响应里补充根 ID
        if not root_id:
            for r in resp_log:
                b = r["body"]
                if not isinstance(b, dict) or b.get("code") != 0:
                    continue
                path = b.get("current_path", [])
                if path and isinstance(path, list):
                    root_id = path[-1].get("folder_id", "")
                    if root_id:
                        break

        if not api_url or not root_id:
            print("\n⚠ 未捕获到知识库 API。")
            print(f"请把 {DEBUG_REQ.name} 和 {DEBUG_RESP.name} 发给我分析。")
            if not using_cdp:
                await browser.close()
            return

        print("\n开始全自动提取书单…\n")
        books = await fetch_folder(page, api_url, base_body, root_id, KB_NAME, 0)

        if not using_cdp:
            await browser.close()

    if not books:
        print(f"\n⚠  未能提取到书目，请把 {DEBUG_REQ.name} 和 {DEBUG_RESP.name} 发给我。")
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
