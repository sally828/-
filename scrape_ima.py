#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本
已知 API：knowledge_tab_reader/get_knowledge_list
已知 KB folder_id：7374371035301653
"""

import asyncio
import csv
import json
import re
from collections import Counter
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext

# ── 配置 ──────────────────────────────────────────────────────────────────────
KB_NAME      = "追梦人的财经图书馆"
KB_FOLDER_ID = "7374371035301653"          # 已从分享API确认
MEMBER_API   = "https://ima.qq.com/cgi-bin/knowledge_tab_reader/get_knowledge_list"
PROXY        = "http://127.0.0.1:10808"
CDP_URL      = "http://localhost:9222"
OUTPUT_CSV   = Path(__file__).parent / "ima_booklist.csv"
DEBUG_RESP   = Path(__file__).parent / "ima_raw.json"
# ─────────────────────────────────────────────────────────────────────────────


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def is_book_name(name: str) -> bool:
    name = name.strip()
    return len(name) >= 3 and not name.startswith("http")


async def async_input(prompt: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)


async def js_post(page, url: str, body: dict) -> dict:
    try:
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
    except Exception as e:
        return {"code": -1, "error": str(e)}


async def probe_folder_param(page, folder_id: str) -> str | None:
    """
    探测调用 MEMBER_API 进入子文件夹时正确的参数字段名。
    返回有效的字段名（如 'folder_id'），或 None。
    """
    # 每种参数名都分别试 有/无 limit
    candidates = [
        ("folder_id",          {"folder_id": folder_id,          "cursor": "", "limit": 20}),
        ("folder_id-nolimit",  {"folder_id": folder_id,          "cursor": ""}),
        ("kb_id",              {"kb_id": folder_id,              "cursor": "", "limit": 20}),
        ("knowledge_id",       {"knowledge_id": folder_id,       "cursor": "", "limit": 20}),
        ("knowledge_base_id",  {"knowledge_base_id": folder_id,  "cursor": "", "limit": 20}),
        ("node_id",            {"node_id": folder_id,            "cursor": "", "limit": 20}),
        ("parent_id",          {"parent_id": folder_id,          "cursor": "", "limit": 20}),
        ("id",                 {"id": folder_id,                 "cursor": "", "limit": 20}),
    ]
    for label, body in candidates:
        result = await js_post(page, MEMBER_API, body)
        code  = result.get("code", -1)
        items = result.get("knowledge_list", [])
        print(f"  [{label}] code={code}, items={len(items)}")
        if code == 0 and items:
            param_name = label.split("-")[0]   # strip "-nolimit" suffix
            print(f"  ✅ 有效格式：{body}")
            return param_name
        await asyncio.sleep(0.3)
    return None


async def fetch_folder(page, folder_param: str, folder_id: str,
                       folder_name: str, depth: int = 0) -> list:
    books, cursor, indent = [], "", "  " * depth
    print(f"{indent}📁 {folder_name}")
    while True:
        body = {folder_param: folder_id, "cursor": cursor}
        data = await js_post(page, MEMBER_API, body)

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
                fid = (item.get("folder_id") or item.get("id")
                       or item.get("kb_id") or item.get("knowledge_id") or "")
                if fid and name:
                    books.extend(await fetch_folder(page, folder_param,
                                                    fid, name, depth + 1))
            elif is_book_name(name):
                books.append({"书名": name, "分类": folder_name})

        next_cur = data.get("next_cursor", "")
        if data.get("is_end", True) or not items or not next_cur or next_cur == cursor:
            break
        cursor = next_cur
        await asyncio.sleep(0.3)
    return books


def register_on_page(p, req_bodies: dict, api_responses: list):
    def on_req(request):
        if "ima.qq.com/cgi-bin" not in request.url:
            return
        try:
            body = json.loads(request.post_data or "{}")
        except Exception:
            body = {}
        req_bodies[request.url] = body

    async def on_resp(response):
        if "ima.qq.com" not in response.url:
            return
        try:
            if "json" not in response.headers.get("content-type", ""):
                return
            rb = await response.json()
            api_responses.append({
                "url": response.url,
                "req_body": req_bodies.get(response.url, {}),
                "body": rb,
            })
        except Exception:
            pass

    p.on("request",  on_req)
    p.on("response", on_resp)


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

    async with async_playwright() as pw:
        using_cdp = False
        ctx: BrowserContext | None = None

        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            ctx  = browser.contexts[0] if browser.contexts else None
            page = None
            if ctx:
                for p in ctx.pages:
                    if "ima.qq.com" in p.url and "/wiki" not in p.url:
                        page = p
                        break
                if page is None:
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            else:
                page = await browser.new_page()
            using_cdp = True
            print(f"✅ 已连接到 360浏览器（CDP）")
        except Exception:
            print("未检测到 360浏览器，使用内置 Chromium…")
            browser = await pw.chromium.launch(headless=False, args=browser_args)
            ctx  = await browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"))
            page = await ctx.new_page()

        # ── 监控所有标签页（含新开的）────────────────────────────────────────
        if ctx:
            for p in ctx.pages:
                register_on_page(p, req_bodies, api_responses)
            ctx.on("page", lambda np: register_on_page(np, req_bodies, api_responses))
        else:
            register_on_page(page, req_bodies, api_responses)

        if not using_cdp:
            await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40000)
            print("请登录腾讯账号后按 Enter")
            await async_input(">>> ")

        # ── 导航到主页并点击进入 KB ───────────────────────────────────────────
        print("\n正在导航到 IMA 主页…")
        await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(3)

        print("步骤1：点击「个人知识库」…")
        ok = await try_click(page, [':text-is("个人知识库")', 'text="个人知识库"', ':text("个人知识库")'])
        print(f"  {'✅' if ok else '（已展开）'}")
        await asyncio.sleep(3)

        print("步骤2：点击「共享知识库」…")
        await try_click(page, [':text-is("共享知识库")', 'text="共享知识库"', ':text("共享知识库")'], timeout_ms=2000)
        await asyncio.sleep(2)

        print(f"步骤3：点击「{KB_NAME}」…")
        ok = await try_click(page, [
            f':text-is("{KB_NAME}")', f'text="{KB_NAME}"', f':text("{KB_NAME}")',
            f'[title="{KB_NAME}"]', f'span:has-text("{KB_NAME}")', f'a:has-text("{KB_NAME}")',
        ], timeout_ms=5000)

        if ok:
            print("  ✅ 已自动点击")
        else:
            print(f"\n  ⚠ 自动点击失败")
            print(f"  请在 360 浏览器里点击左侧「{KB_NAME}」（共享知识库下）")
            print(f"  点好后按 Enter（所有标签页 API 均被监控）…")
            await async_input("  >>> ")

        print("\n等待 10 秒…")
        await asyncio.sleep(10)

        # ── 打印所有标签页 ───────────────────────────────────────────────────
        all_pages = ctx.pages if ctx else [page]
        print(f"\n浏览器当前共 {len(all_pages)} 个标签页：")
        for i, p in enumerate(all_pages):
            print(f"  {i+1}. {p.url}")

        # 保存调试数据
        DEBUG_RESP.write_text(
            json.dumps(api_responses[:50], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"共拦截到 {len(api_responses)} 个 API 响应")

        # ── 打印所有含非空列表的响应（不限于 knowledge_list）──────────────────
        print("\n含列表数据的响应：")
        for r in api_responses:
            b = r["body"]
            if not isinstance(b, dict) or b.get("code") != 0:
                continue
            for key, val in b.items():
                if isinstance(val, list) and len(val) > 0 and key != "current_path":
                    first = val[0]
                    preview = json.dumps(first, ensure_ascii=False)[:80] if isinstance(first, dict) else str(first)[:80]
                    print(f"  {r['url'].split('/')[-1]:40s} .{key}={len(val)}项  first={preview}")
                    break

        # ── 找 KB 标签页（wikis URL）────────────────────────────────────────
        kb_page = page
        for p in all_pages:
            if "wikis" in p.url or "wiki" in p.url:
                kb_page = p
                break
        print(f"\n使用标签页：{kb_page.url}")

        # ── 直接用已知参数探测 ────────────────────────────────────────────────
        print(f"\n探测子文件夹参数名（folder_id = {KB_FOLDER_ID}）…")
        folder_param = await probe_folder_param(kb_page, KB_FOLDER_ID)

        if folder_param is None:
            # 尝试从拦截到的响应里找
            print("\n探测失败，在拦截响应中查找 knowledge_list 不为空的 API…")
            for r in api_responses:
                b = r["body"]
                if isinstance(b, dict) and b.get("code") == 0 and b.get("knowledge_list"):
                    print(f"  在响应中找到：{r['url']}")
                    print(f"  请求体：{r['req_body']}")
                    print(f"  第一条：{b['knowledge_list'][0].get('name', '')[:60]}")
                    break
            print(f"\n⚠ 无法确定参数格式，请把 {DEBUG_RESP.name} 发给我分析。")
            if not using_cdp:
                await browser.close()
            return

        print(f"\n✅ 参数名确认：{folder_param}，开始递归提取…\n")
        books = await fetch_folder(kb_page, folder_param, KB_FOLDER_ID, KB_NAME, 0)

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
