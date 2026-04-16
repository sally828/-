#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本
策略：
  1. 监控浏览器打开追梦人KB时自动发出的 get_knowledge_list，拦截22个顶层分类
     （顶层分类 item 结构：media_type=99/文件夹，ID 在 media_id 字段）
  2. 首次查询子文件夹时自动探测正确的参数格式，之后复用
  3. folder 判断：media_type==99 或 media_id 以 "folder_" 开头
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
KB_FOLDER_ID = "7374371035301653"          # 根知识库 ID
MEMBER_API   = "https://ima.qq.com/cgi-bin/knowledge_tab_reader/get_knowledge_list"
PROXY        = "http://127.0.0.1:10808"
CDP_URL      = "http://localhost:9222"
OUTPUT_CSV   = Path(__file__).parent / "ima_booklist.csv"
DEBUG_RESP   = Path(__file__).parent / "ima_raw.json"
# ─────────────────────────────────────────────────────────────────────────────

# 首次成功查询后缓存参数模板，"__ID__" 为 folder_id 占位符
_FOLDER_BODY_TPL: dict | None = None


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


def register_on_page(p, api_responses: list):
    async def on_resp(response):
        if "ima.qq.com" not in response.url:
            return
        try:
            if "json" not in response.headers.get("content-type", ""):
                return
            rb = await response.json()
            try:
                req_body = json.loads(response.request.post_data or "{}")
            except Exception:
                req_body = {}
            api_responses.append({"url": response.url, "req_body": req_body, "body": rb})
        except Exception:
            pass

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


def extract_id(item: dict) -> str:
    """
    从 item 中提取文件夹 ID。
    IMA 文件夹的 ID 在 media_id（如 "folder_7434760334894700"）。
    """
    # 顶层字段：media_id 优先（实际观察到的字段）
    for field in ["media_id", "knowledge_base_id", "folder_id",
                  "id", "kb_id", "knowledge_id", "node_id"]:
        val = item.get(field)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
        if val and isinstance(val, int) and val != 0:
            return str(val)
    # 嵌套字段 folder_info.folder_id
    fi = item.get("folder_info")
    if isinstance(fi, dict):
        val = fi.get("folder_id") or fi.get("id")
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def is_folder_item(item: dict) -> bool:
    """判断 item 是否为文件夹（media_type=99 或 media_id 以 folder_ 开头）。"""
    if item.get("media_type") == 99:
        return True
    mid = item.get("media_id", "")
    if isinstance(mid, str) and mid.startswith("folder_"):
        return True
    itype = item.get("type")
    if itype in (2, "folder", "dir", "directory"):
        return True
    return bool(item.get("is_folder"))


def make_body(folder_id: str, cursor: str) -> dict:
    body = {k: (folder_id if v == "__ID__" else v)
            for k, v in _FOLDER_BODY_TPL.items()}
    body["cursor"] = cursor
    return body


async def probe_folder_api(page, folder_id: str) -> bool:
    """
    探测查询文件夹内容的正确参数格式，成功后缓存到 _FOLDER_BODY_TPL。
    尝试顺序：
      1. {"folder_id": X}
      2. {"knowledge_base_id": ROOT, "folder_id": X}
      3. {"knowledge_base_id": X}
      4. {"parent_folder_id": X}
    """
    global _FOLDER_BODY_TPL
    print(f"  探测文件夹 API 参数（folder_id={folder_id}）…")
    candidates = [
        {"folder_id": folder_id},
        {"knowledge_base_id": KB_FOLDER_ID, "folder_id": folder_id},
        {"knowledge_base_id": folder_id},
        {"parent_folder_id": folder_id},
    ]
    for probe in candidates:
        body = dict(probe)
        body["cursor"] = ""
        d    = await js_post(page, MEMBER_API, body)
        code = d.get("code", -1)
        kl   = d.get("knowledge_list", [])
        print(f"    {body} → code={code}, items={len(kl)}")
        if code == 0:
            tpl = {k: ("__ID__" if v == folder_id else v) for k, v in probe.items()}
            _FOLDER_BODY_TPL = tpl
            print(f"  ✅ 参数模板：{tpl}")
            return True
        await asyncio.sleep(0.3)
    return False


async def fetch_category(page, folder_id: str, cat_name: str, depth: int = 0) -> list:
    global _FOLDER_BODY_TPL

    books    = []
    cursor   = ""
    indent   = "  " * depth
    page_num = 0
    printed_first = False

    print(f"{indent}📁 {cat_name}  (id={folder_id})")

    if _FOLDER_BODY_TPL is None:
        ok = await probe_folder_api(page, folder_id)
        if not ok:
            print(f"{indent}  ⚠ 无法确定 API 参数，跳过此分类")
            return books

    while True:
        body = make_body(folder_id, cursor)
        data = await js_post(page, MEMBER_API, body)

        code  = data.get("code", -1)
        items = data.get("knowledge_list", [])
        page_num += 1

        if code != 0:
            msg = data.get("msg", data.get("error", ""))
            print(f"{indent}  ⚠ code={code}: {msg}")
            break

        if not printed_first and items:
            printed_first = True
            print(f"{indent}  [首条 item 结构 depth={depth}]")
            print(json.dumps(items[0], ensure_ascii=False, indent=4))

        print(f"{indent}  第{page_num}页：{len(items)} 条")

        for item in items:
            if not isinstance(item, dict):
                continue
            name = clean(item.get("name") or item.get("title") or "")
            if is_folder_item(item):
                fid = extract_id(item)
                if fid and name:
                    sub = await fetch_category(page, fid, name, depth + 1)
                    books.extend(sub)
                else:
                    print(f"{indent}    ⚠ 子文件夹 fid={fid!r} name={name!r}，跳过")
            elif is_book_name(name):
                books.append({"书名": name, "分类": cat_name})

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

    api_responses: list = []

    async with async_playwright() as pw:
        using_cdp = False
        ctx: BrowserContext | None = None

        try:
            browser  = await pw.chromium.connect_over_cdp(CDP_URL)
            ctx      = browser.contexts[0] if browser.contexts else None
            page     = None
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
            print("✅ 已连接到 360浏览器（CDP）")
        except Exception:
            print("未检测到 360浏览器，使用内置 Chromium…")
            browser = await pw.chromium.launch(headless=False, args=browser_args)
            ctx  = await browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"))
            page = await ctx.new_page()

        if ctx:
            for p in ctx.pages:
                register_on_page(p, api_responses)
            ctx.on("page", lambda np: register_on_page(np, api_responses))
        else:
            register_on_page(page, api_responses)

        if not using_cdp:
            await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40000)
            print("请登录腾讯账号后按 Enter")
            await async_input(">>> ")

        print("\n正在导航到 IMA 主页…")
        await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(3)

        print("步骤1：点击「个人知识库」…")
        ok = await try_click(page, [':text-is("个人知识库")',
                                    'text="个人知识库"', ':text("个人知识库")'])
        print(f"  {'✅' if ok else '（已展开）'}")
        await asyncio.sleep(3)

        print("步骤2：点击「共享知识库」…")
        await try_click(page, [':text-is("共享知识库")',
                                'text="共享知识库"', ':text("共享知识库")'], timeout_ms=2000)
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
            print(f"  请在 360 浏览器里手动点击左侧「{KB_NAME}」（共享知识库下）")
            print(f"  点好后按 Enter…")
            await async_input("  >>> ")

        print("\n等待 10 秒，让知识库页面加载完成…")
        await asyncio.sleep(10)

        all_pages = ctx.pages if ctx else [page]
        print(f"\n浏览器当前共 {len(all_pages)} 个标签页：")
        for i, p in enumerate(all_pages):
            print(f"  {i+1}. {p.url}")

        DEBUG_RESP.write_text(
            json.dumps(api_responses[:60], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"共拦截到 {len(api_responses)} 个 API 响应")

        print("\n含列表数据的响应：")
        for r in api_responses:
            b = r["body"]
            if not isinstance(b, dict) or b.get("code") != 0:
                continue
            for key, val in b.items():
                if isinstance(val, list) and len(val) > 0 and key != "current_path":
                    first   = val[0]
                    preview = (json.dumps(first, ensure_ascii=False)[:120]
                               if isinstance(first, dict) else str(first)[:120])
                    req_p   = json.dumps(r["req_body"], ensure_ascii=False)[:60]
                    print(f"  {r['url'].split('/')[-1]:40s} .{key}={len(val)}项  req={req_p}")
                    print(f"    first={preview}")
                    break

        kb_page = page
        for p in all_pages:
            if "wikis" in p.url or "wiki" in p.url:
                kb_page = p
                break
        print(f"\n使用标签页：{kb_page.url}")

        # ── 从拦截数据中找顶层分类（knowledge_list 最多的那条）────────────────
        print("\n在拦截数据中查找顶层分类…")
        top_items    = None
        top_req_body = None

        for r in api_responses:
            if "get_knowledge_list" not in r["url"]:
                continue
            b  = r.get("body", {})
            kl = b.get("knowledge_list", []) if isinstance(b, dict) and b.get("code") == 0 else []
            if len(kl) > len(top_items or []):
                top_items    = kl
                top_req_body = r.get("req_body", {})

        if top_items:
            print(f"✅ 找到 {len(top_items)} 个顶层分类")
            print(f"   对应请求体：{json.dumps(top_req_body, ensure_ascii=False)}")
        else:
            print("⚠ 拦截数据中无顶层分类，尝试直接调用…")
            for try_body in [
                {"knowledge_base_id": KB_FOLDER_ID, "cursor": ""},
                {"knowledge_base_id": KB_FOLDER_ID},
                {"folder_id": KB_FOLDER_ID, "cursor": ""},
                {},
            ]:
                data = await js_post(kb_page, MEMBER_API, try_body)
                kl   = data.get("knowledge_list", [])
                code = data.get("code", -1)
                print(f"  {json.dumps(try_body)} → code={code}, items={len(kl)}")
                if code == 0 and kl:
                    top_items    = kl
                    top_req_body = try_body
                    print(f"  ✅ 找到 {len(kl)} 个分类！")
                    break
                await asyncio.sleep(0.3)

        if not top_items:
            print(f"\n⚠ 所有方式均未获取到顶层分类，请把 {DEBUG_RESP.name} 发给我分析。")
            if not using_cdp:
                await browser.close()
            return

        print("\n第一个顶层分类完整字段结构：")
        print(json.dumps(top_items[0], ensure_ascii=False, indent=2))

        print(f"\n所有 {len(top_items)} 个顶层分类：")
        for i, item in enumerate(top_items, 1):
            nm  = clean(item.get("name") or item.get("title") or "?")
            cid = extract_id(item)
            print(f"  {i:2d}. {nm}  (id={cid})")

        # ── 递归提取每个分类的书目 ────────────────────────────────────────────
        print(f"\n开始递归提取书目…\n")
        books = []
        for item in top_items:
            cat_name = clean(item.get("name") or item.get("title") or "")
            cat_id   = extract_id(item)
            if not cat_name or not cat_id:
                print(f"  ⚠ 跳过：name={cat_name!r} id={cat_id!r}")
                continue
            cat_books = await fetch_category(kb_page, cat_id, cat_name, depth=1)
            books.extend(cat_books)
            print(f"  ✓ {cat_name}：共 {len(cat_books)} 本\n")
            await asyncio.sleep(0.5)

        if not using_cdp:
            await browser.close()

    if not books:
        print(f"\n⚠ 未提取到书目，请把 {DEBUG_RESP.name} 发给我分析。")
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
