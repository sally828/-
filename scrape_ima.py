#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本
方案A（优先）：直接调用分享 API + folder_id，全自动无需点击
方案B（备用）：拦截成员视角 API（需手动点击知识库）
"""

import asyncio
import csv
import json
import re
from collections import Counter
from pathlib import Path

from playwright.async_api import async_playwright

# ── 已知参数 ──────────────────────────────────────────────────────────────────
KB_NAME   = "追梦人的财经图书馆"
SHARE_URL = "https://ima.qq.com/wiki/?shareId=80155f2d7bdbe7249ae15f61cb22c37f997fb4dc0f8f60dfd70185a94e4905f5"
SHARE_ID  = "80155f2d7bdbe7249ae15f61cb22c37f997fb4dc0f8f60dfd70185a94e4905f5"
ROOT_ID   = "7374371035301653"
SHARE_API = "https://ima.qq.com/cgi-bin/knowledge_share_get/get_share_info"

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
    """非阻塞 input，不会冻结 asyncio 事件循环"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)


async def js_post(page, url: str, body: dict) -> dict:
    """在浏览器 cookie 上下文里 POST，自动携带登录态"""
    return await page.evaluate("""
        async ([url, body]) => {
            try {
                const r = await fetch(url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify(body)
                });
                return await r.json();
            } catch(e) { return {code: -1, error: e.toString()}; }
        }
    """, [url, body]) or {}


async def fetch_folder(page, api_url: str, base_body: dict,
                       folder_id: str, folder_name: str, depth: int = 0) -> list:
    books, cursor, indent = [], "", "  " * depth
    print(f"{indent}📁 {folder_name}")
    while True:
        data = await js_post(page, api_url, {**base_body, "folder_id": folder_id, "cursor": cursor})

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
                    books.extend(await fetch_folder(page, api_url, base_body, fid, name, depth + 1))
            elif is_book_name(name):
                books.append({"书名": name, "分类": folder_name})

        next_cur = data.get("next_cursor", "")
        if data.get("is_end", True) or not items or not next_cur or next_cur == cursor:
            break
        cursor = next_cur
        await asyncio.sleep(0.3)
    return books


# ── 方案A：直接调用分享 API ────────────────────────────────────────────────────

async def plan_a(page) -> list | None:
    """
    导航到分享链接，建立 auth cookie，然后带 folder_id 直接调用 get_share_info。
    返回书目列表；若API不返回内容则返回 None（转方案B）。
    """
    print("\n=== 方案A：分享 API 直接调用 ===")
    print(f"  导航到分享链接建立 auth 上下文…")
    try:
        await page.goto(SHARE_URL, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(3)
    except Exception as e:
        print(f"  导航失败: {e}")
        return None

    # 尝试多种 body 格式
    candidates = [
        {"folder_id": ROOT_ID, "cursor": "", "limit": 50},
        {"share_id": SHARE_ID, "folder_id": ROOT_ID, "cursor": "", "limit": 50},
        {"folder_id": ROOT_ID, "cursor": ""},
        {"share_id": SHARE_ID, "folder_id": ROOT_ID, "cursor": ""},
        {"folder_id": ROOT_ID, "cursor": "", "count": 50},
    ]

    probe_log = []
    for body in candidates:
        result = await js_post(page, SHARE_API, body)
        items  = result.get("knowledge_list", [])
        entry  = {"body": body, "code": result.get("code"), "items": len(items),
                  "is_end": result.get("is_end"), "cursor": result.get("next_cursor", "")[:20]}
        probe_log.append(entry)
        print(f"  {json.dumps(body, ensure_ascii=False)[:60]}")
        print(f"    => code={result.get('code')}, {len(items)} 条, is_end={result.get('is_end')}")

        if result.get("code") == 0 and items:
            print(f"\n  ✅ 方案A成功！开始递归提取…")
            DEBUG_RESP.write_text(json.dumps(probe_log, ensure_ascii=False, indent=2), encoding="utf-8")
            base = {k: v for k, v in body.items() if k not in ("folder_id", "cursor")}
            return await fetch_folder(page, SHARE_API, base, ROOT_ID, KB_NAME, 0)

        await asyncio.sleep(0.5)

    print(f"\n  方案A未能获取内容（所有格式均返回空列表）")
    DEBUG_RESP.write_text(json.dumps(probe_log, ensure_ascii=False, indent=2), encoding="utf-8")
    return None


# ── 方案B：拦截成员视角 API ────────────────────────────────────────────────────

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


async def plan_b(page) -> tuple[str, dict, str] | tuple[None, None, None]:
    """
    导航到 ima.qq.com 主页，自动点击侧栏进入知识库，拦截 API。
    返回 (api_url, base_body, root_id)；失败返回 (None, None, None)。
    """
    print("\n=== 方案B：拦截成员视角 API ===")

    req_bodies    = {}
    api_responses = []

    def on_request(request):
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
            if "json" not in response.headers.get("content-type", ""):
                return
            resp_body = await response.json()
            api_responses.append({
                "url":      response.url,
                "req_body": req_bodies.get(response.url, {}),
                "body":     resp_body,
            })
        except Exception:
            pass

    page.on("request",  on_request)
    page.on("response", on_response)

    print("  导航到 IMA 主页…")
    await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40000)
    await asyncio.sleep(3)

    print("  点击「个人知识库」…")
    ok = await try_click(page, [
        ':text-is("个人知识库")', 'text="个人知识库"', ':text("个人知识库")',
    ])
    print(f"  {'✅' if ok else '（已展开或未找到）'}")
    await asyncio.sleep(3)

    print("  点击「共享知识库」（如有）…")
    await try_click(page, [
        ':text-is("共享知识库")', 'text="共享知识库"', ':text("共享知识库")',
    ], timeout_ms=2000)
    await asyncio.sleep(2)

    print(f"  点击「{KB_NAME}」…")
    ok = await try_click(page, [
        f':text-is("{KB_NAME}")',
        f'text="{KB_NAME}"',
        f':text("{KB_NAME}")',
        f'[title="{KB_NAME}"]',
        f'span:has-text("{KB_NAME}")',
        f'a:has-text("{KB_NAME}")',
    ], timeout_ms=5000)

    if ok:
        print("  ✅ 已自动点击，等待 10 秒…")
        await asyncio.sleep(10)
    else:
        print(f"\n  ⚠ 自动点击失败。")
        print(f"  请在 360 浏览器里手动点击左侧「{KB_NAME}」")
        print(f"  路径：个人知识库 → 共享知识库 → {KB_NAME}")
        print(f"  点好后按 Enter（等待时 API 继续被拦截）…")
        await async_input("  >>> ")
        await asyncio.sleep(5)

    # 保存调试数据
    DEBUG_RESP.write_text(
        json.dumps(api_responses[:40], ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"  拦截到 {len(api_responses)} 个 API 响应")

    for r in api_responses:
        b = r["body"]
        if not isinstance(b, dict) or b.get("code") != 0:
            continue
        items = b.get("knowledge_list", [])
        if not items:
            continue
        api_url  = r["url"]
        req_body = r["req_body"]
        print(f"\n  ✅ 找到知识库 API：{api_url}")
        print(f"     请求体：{json.dumps(req_body, ensure_ascii=False)}")
        print(f"     第一条：{items[0].get('name', '')[:60]}")
        path    = b.get("current_path", [])
        root_id = path[0].get("folder_id", "") if path else ""
        if not root_id:
            root_id = req_body.get("folder_id") or req_body.get("knowledge_base_id") or ""
        base_body = {k: v for k, v in req_body.items() if k not in ("folder_id", "cursor")}
        return api_url, base_body, root_id

    print(f"\n  方案B也未捕获到有内容的 API。")
    for r in api_responses:
        b = r["body"]
        if isinstance(b, dict):
            print(f"  {r['url'][:80]}  code={b.get('code')}  items={len(b.get('knowledge_list', []))}")
    print(f"\n  请把 {DEBUG_RESP.name} 发给我分析。")
    return None, None, None


# ── 主流程 ────────────────────────────────────────────────────────────────────

async def main():
    browser_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    if PROXY:
        browser_args.append(f"--proxy-server={PROXY}")

    async with async_playwright() as pw:
        using_cdp = False
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            ctx  = browser.contexts[0] if browser.contexts else None
            page = None
            if ctx:
                for p in ctx.pages:
                    if "ima.qq.com" in p.url and "/wiki/" not in p.url:
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

        # ── 方案A：直接调用分享 API（全自动，无需点击）────────────────────────
        books = await plan_a(page)

        # ── 方案B：拦截成员视角 API（备用）───────────────────────────────────
        if books is None:
            result = await plan_b(page)
            if result[0]:
                api_url, base_body, root_id = result
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
