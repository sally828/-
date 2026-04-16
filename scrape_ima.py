#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本
以成员身份访问共享知识库，自动拦截真实 API 并提取完整书单
"""

import asyncio
import csv
import json
import re
from collections import Counter
from pathlib import Path

from playwright.async_api import async_playwright

# ── 配置 ──────────────────────────────────────────────────────────────────────
IMA_HOME  = "https://ima.qq.com"
KB_NAME   = "追梦人的财经图书馆"
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


async def call_api(page, url: str, body: dict) -> dict:
    """用浏览器登录态调用任意 IMA API"""
    return await page.evaluate("""
        async (args) => {
            try {
                const resp = await fetch(args.url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify(args.body)
                });
                return await resp.json();
            } catch(e) {
                return {code: -1, error: e.toString()};
            }
        }
    """, {"url": url, "body": body}) or {}


async def fetch_folder(page, api_url: str, base_body: dict,
                       folder_id: str, folder_name: str,
                       depth: int = 0) -> list[dict]:
    """递归获取文件夹内所有书目"""
    books = []
    cursor = ""
    indent = "  " * depth

    while True:
        body = {**base_body, "folder_id": folder_id, "cursor": cursor}
        data = await call_api(page, api_url, body)

        code = data.get("code", -1)
        if code != 0:
            print(f"{indent}  ⚠ code={code}: {data.get('msg', data.get('error', ''))}")
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
                    sub = await fetch_folder(page, api_url, base_body,
                                             fid, name, depth + 1)
                    books.extend(sub)
            else:
                if is_book_name(name):
                    books.append({"书名": name, "分类": folder_name})

        if data.get("is_end", True) or not items:
            break

        next_cur = data.get("next_cursor", "")
        if not next_cur or next_cur == cursor:
            break

        cursor = next_cur
        await asyncio.sleep(0.3)

    return books


async def main():
    browser_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    if PROXY:
        browser_args.append(f"--proxy-server={PROXY}")

    raw_api: list[dict] = []

    async def on_response(response):
        url = response.url
        if "ima.qq.com" not in url:
            return
        try:
            if "json" not in response.headers.get("content-type", ""):
                return
            body = await response.json()
            raw_api.append({"url": url, "body": body})
        except Exception:
            pass

    async with async_playwright() as pw:
        using_cdp = False
        page = None

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

        if not using_cdp:
            print("\n请在浏览器里登录腾讯账号，完成后按 Enter")
            await page.goto(IMA_HOME, wait_until="domcontentloaded", timeout=40000)
            input(">>> ")

        # ── 导航到 IMA 首页，拦截 API 调用 ──────────────────────────────────
        page.on("response", on_response)

        print(f"\n正在打开 IMA 首页…")
        await page.goto(IMA_HOME, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(2)

        # ── 点击「追梦人的财经图书馆」 ────────────────────────────────────────
        print(f"正在点击「{KB_NAME}」…")
        clicked = False
        for selector in [
            f'text="{KB_NAME}"',
            f':text("{KB_NAME}")',
            f'[title="{KB_NAME}"]',
        ]:
            try:
                await page.click(selector, timeout=4000)
                clicked = True
                print("  ✅ 点击成功")
                break
            except Exception:
                pass

        if not clicked:
            print(f"  未能自动点击，请手动点击左侧「{KB_NAME}」后按 Enter")
            input("  >>> ")

        await asyncio.sleep(4)   # 等待 API 响应到来

        # ── 分析拦截到的 API，找出知识库内容接口 ──────────────────────────────
        DEBUG_JSON.write_text(
            json.dumps(raw_api[:30], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"\n拦截到 {len(raw_api)} 个 API 响应，正在分析…")

        # 找包含 knowledge_list 的响应，提取 API 模式
        api_url = None
        base_body: dict = {}
        root_folder_id = ""

        for r in raw_api:
            url = r["url"]
            body = r["body"]
            if not isinstance(body, dict) or body.get("code") != 0:
                continue
            if "knowledge_list" in body or "current_path" in body:
                api_url = url
                # 提取根文件夹 ID
                path = body.get("current_path", [])
                if path and isinstance(path, list):
                    root_folder_id = path[-1].get("folder_id", "")
                print(f"  找到 API：{url}")
                print(f"  根文件夹 ID：{root_folder_id}")
                break

        if not api_url:
            print("\n⚠ 未能捕获到知识库 API。")
            print(f"请把 {DEBUG_JSON.name} 发给我分析。")
            if not using_cdp:
                await browser.close()
            return

        # 构建基础请求体（从拦截的 URL 路径判断参数）
        # 尝试常见参数组合
        for limit_key in ("limit", "count"):
            base_body = {limit_key: 50}
            # 如果是 share 接口，加 share_id
            if "share" in api_url:
                share_id = "80155f2d7bdbe7249ae15f61cb22c37f997fb4dc0f8f60dfd70185a94e4905f5"
                base_body["share_id"] = share_id
            else:
                # 成员接口，可能需要 knowledge_base_id
                base_body["knowledge_base_id"] = root_folder_id

            test = await call_api(page, api_url,
                                  {**base_body, "folder_id": root_folder_id, "cursor": ""})
            if test.get("code") == 0:
                print(f"  参数验证成功（{limit_key}=50）")
                break
            print(f"  {limit_key}=50 失败（code={test.get('code')}），尝试下一种…")
        else:
            print("\n⚠ 参数构造失败，请把 ima_raw.json 发给我分析。")
            if not using_cdp:
                await browser.close()
            return

        # ── 递归提取所有书目 ──────────────────────────────────────────────────
        print("\n开始全自动提取书单（无需手动操作）…\n")
        books = await fetch_folder(page, api_url, base_body,
                                   root_folder_id, KB_NAME, 0)

        if not using_cdp:
            await browser.close()

    # ── 保存结果 ──────────────────────────────────────────────────────────────
    if not books:
        print("\n⚠  未能提取到书目，请把 ima_raw.json 发给我分析。")
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
