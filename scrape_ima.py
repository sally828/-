#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本
路径：ima.qq.com 主页（已登录）→ 个人知识库 → 共享知识库 → 追梦人的财经图书馆
拦截成员视角 API，递归提取完整书单
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
DEBUG_RESP = Path(__file__).parent / "ima_raw.json"
# ─────────────────────────────────────────────────────────────────────────────


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def is_book_name(name: str) -> bool:
    name = name.strip()
    return len(name) >= 3 and not name.startswith("http")


async def async_input(prompt: str) -> str:
    """非阻塞 input —— 不锁死 asyncio 事件循环，拦截器可以继续运行"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)


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
    print(f"{indent}📁 {folder_name}")
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
            name  = clean(item.get("name") or item.get("title") or "")
            itype = item.get("type")
            if itype in (2, "folder", "dir", "directory"):
                fid = item.get("folder_id") or item.get("id") or ""
                if fid and name:
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


async def try_click(page, selectors: list, timeout_ms: int = 3000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=timeout_ms):
                await loc.click(timeout=timeout_ms)
                return True
        except Exception:
            pass
    return False


async def main():
    browser_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    if PROXY:
        browser_args.append(f"--proxy-server={PROXY}")

    req_bodies    = {}
    api_responses = []

    async def on_request(request):
        if "ima.qq.com/cgi-bin" not in request.url:
            return
        try:
            body = json.loads(request.post_data or "{}")
        except Exception:
            body = {}
        req_bodies[request.url] = body

    async def on_response(response):
        if "ima.qq.com" not in response.url:
            return
        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            resp_body = await response.json()
            api_responses.append({
                "url":      response.url,
                "req_body": req_bodies.get(response.url, {}),
                "body":     resp_body,
            })
        except Exception:
            pass

    async with async_playwright() as pw:
        using_cdp = False
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            ctx  = browser.contexts[0] if browser.contexts else None
            page = None
            if ctx:
                for p in ctx.pages:
                    u = p.url
                    if "ima.qq.com" in u and "/wiki/" not in u and "ima.copilot" not in u:
                        page = p
                        break
                if page is None:
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            else:
                page = await browser.new_page()
            using_cdp = True
            print(f"✅ 已连接到 360浏览器（CDP），当前页面：{page.url}")
        except Exception:
            print("未检测到 360浏览器，使用内置 Chromium…")
            browser = await pw.chromium.launch(headless=False, args=browser_args)
            ctx  = await browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"))
            page = await ctx.new_page()

        if not using_cdp:
            await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40000)
            print("请登录腾讯账号后按 Enter")
            await async_input(">>> ")

        # 注册拦截器（必须在导航前注册）
        page.on("request",  on_request)
        page.on("response", on_response)

        # 导航到 ima.qq.com 主页（清空历史导航，确保从头开始点击）
        print("\n正在导航到 IMA 主页…")
        await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(3)
        print(f"当前页面：{page.url}")

        # ── 步骤1：点击「个人知识库」展开 ────────────────────────────────────
        print("\n步骤1：展开「个人知识库」…")
        ok = await try_click(page, [
            ':text-is("个人知识库")',
            'text="个人知识库"',
            ':text("个人知识库")',
        ])
        print(f"  {'✅ 已点击' if ok else '（可能已展开）'}")
        await asyncio.sleep(3)   # 等待侧栏展开（React re-render 需要时间）

        # ── 步骤2（可选）：点击「共享知识库」展开（如果需要）─────────────────
        print("\n步骤2：展开「共享知识库」（如有）…")
        ok2 = await try_click(page, [
            ':text-is("共享知识库")',
            'text="共享知识库"',
            ':text("共享知识库")',
        ], timeout_ms=2000)
        if ok2:
            print("  ✅ 已点击「共享知识库」")
            await asyncio.sleep(2)
        else:
            print("  （无需点击或已展开）")

        # ── 步骤3：点击「追梦人的财经图书馆」────────────────────────────────
        print(f"\n步骤3：点击「{KB_NAME}」…")
        ok3 = await try_click(page, [
            f':text-is("{KB_NAME}")',
            f'text="{KB_NAME}"',
            f':text("{KB_NAME}")',
            f'[title="{KB_NAME}"]',
            f'span:has-text("{KB_NAME}")',
            f'a:has-text("{KB_NAME}")',
        ], timeout_ms=5000)

        if ok3:
            print(f"  ✅ 已自动点击")
            print("  等待 API 响应（10秒）…")
            await asyncio.sleep(10)
        else:
            print(f"  ⚠ 未能自动点击。")
            print(f"  请在 360 浏览器里手动点击左侧「{KB_NAME}」")
            print(f"  路径：个人知识库 → 共享知识库 → {KB_NAME}")
            print(f"  点好后在这里按 Enter（在等待时 API 仍在被拦截）…")
            await async_input("  >>> ")   # ← 非阻塞，拦截器正常运行
            print("  再等待 5 秒让响应完整接收…")
            await asyncio.sleep(5)

        # 保存所有响应供调试
        DEBUG_RESP.write_text(
            json.dumps(api_responses[:40], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"\n拦截到 {len(api_responses)} 个 API 响应")

        # ── 找包含知识库内容的 API（knowledge_list 非空）────────────────────
        api_url, base_body, root_id = None, {}, ""
        for r in api_responses:
            resp_body = r["body"]
            if not isinstance(resp_body, dict) or resp_body.get("code") != 0:
                continue
            items = resp_body.get("knowledge_list", [])
            if not items:
                continue
            api_url  = r["url"]
            req_body = r["req_body"]
            print(f"\n✅ 找到知识库 API：{api_url}")
            print(f"   请求体：{json.dumps(req_body, ensure_ascii=False)}")
            print(f"   第一条：{items[0].get('name', items[0].get('title', ''))[:60]}")
            path    = resp_body.get("current_path", [])
            root_id = path[0].get("folder_id", "") if path else ""
            if not root_id:
                root_id = (req_body.get("folder_id")
                           or req_body.get("knowledge_base_id")
                           or "")
            base_body = {k: v for k, v in req_body.items()
                         if k not in ("folder_id", "cursor")}
            break

        if not api_url or not root_id:
            print(f"\n⚠ 未捕获到有内容的知识库 API。")
            print(f"   拦截到的所有响应：")
            for r in api_responses:
                b = r["body"]
                if isinstance(b, dict):
                    print(f"   {r['url'][:80]}")
                    print(f"     code={b.get('code')}  items={len(b.get('knowledge_list', []))}")
            print(f"\n请把 {DEBUG_RESP.name} 发给我分析。")
            if not using_cdp:
                await browser.close()
            return

        print(f"\n开始递归提取书单（根目录：{root_id}）…\n")
        books = await fetch_folder(page, api_url, base_body, root_id, KB_NAME, 0)

        if not using_cdp:
            await browser.close()

    if not books:
        print(f"\n⚠ 未提取到书目，请把 {DEBUG_RESP.name} 发给我。")
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
