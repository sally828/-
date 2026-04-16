#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本 v7（终版）
策略：
  1. 拦截浏览器初始化请求，获取22个顶层文件夹
  2. 对每个文件夹：goto wikis根 → 等待元素可见 → 点击 → 等待API响应
  3. 发现子文件夹时自动加入队列继续导航
  4. 自动导航失败的文件夹 → 列出请求用户手动点击
  5. 从所有拦截到的 code=0 响应中提取书目，
     用 item.parent_folder_id 追溯顶层分类（不依赖 req_body）
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
KB_FOLDER_ID = "7374371035301653"
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
    return len(name.strip()) >= 3 and not name.strip().startswith("http")


async def async_input(prompt: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)


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


def extract_id(item: dict) -> str:
    for field in ["media_id", "knowledge_base_id", "folder_id",
                  "id", "kb_id", "knowledge_id", "node_id"]:
        val = item.get(field)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
        if val and isinstance(val, int) and val != 0:
            return str(val)
    fi = item.get("folder_info")
    if isinstance(fi, dict):
        val = fi.get("folder_id") or fi.get("id")
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def is_folder_item(item: dict) -> bool:
    """media_id 以 folder_ 开头才算文件夹；PPT/PDF 等以其他前缀开头不算。"""
    mid = item.get("media_id", "")
    if isinstance(mid, str):
        if mid.startswith("folder_"):
            return True
        if mid:        # 有 media_id 但不是 folder_ 开头 → 文档
            return False
    # 无 media_id 时参考 type 字段
    return item.get("type") in (2, "folder", "dir", "directory") or bool(item.get("is_folder"))


def get_top_category(folder_id: str, id_to_name: dict, id_to_parent: dict) -> str:
    """
    沿 parent_folder_id 链上溯，找到直接挂在根 KB 下的顶层分类名称。
    """
    cur = folder_id
    visited: set = set()
    while cur and cur not in visited:
        visited.add(cur)
        parent = id_to_parent.get(cur, "")
        if not parent or parent == KB_FOLDER_ID:
            return id_to_name.get(cur, cur)
        cur = parent
    return id_to_name.get(cur or folder_id, "未知分类")


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


async def navigate_to_folder(kb_page, wikis_root_url: str, folder_name: str) -> bool:
    """
    强制回到 wikis 根视图（goto），等待文件夹元素出现，再点击。
    使用 wait_for(visible) 应对 React 异步渲染。
    """
    try:
        await kb_page.goto(wikis_root_url, wait_until="domcontentloaded", timeout=20000)
    except Exception:
        pass
    await asyncio.sleep(1)   # 额外 1 秒让 React 开始渲染

    selectors = [
        f':text-is("{folder_name}")',
        f'span:has-text("{folder_name}")',
        f'[title="{folder_name}"]',
        f'li:has-text("{folder_name}")',
        f'div[class*="folder"]:has-text("{folder_name}")',
    ]
    for sel in selectors:
        try:
            loc = kb_page.locator(sel).first
            await loc.wait_for(state="visible", timeout=6000)  # 等最多6秒
            await loc.click()
            return True
        except Exception:
            pass
    return False


def absorb_responses(api_responses: list, start: int,
                     id_to_name: dict, id_to_parent: dict) -> tuple[list, list]:
    """
    处理 api_responses[start:] 中所有 get_knowledge_list code=0 的响应：
    - 更新 id_to_name / id_to_parent（从 item.parent_folder_id 建图）
    - 返回 (books_list, new_folder_items_list)
    """
    books: list   = []
    new_folders: list = []
    seen: set     = set()

    for r in api_responses[start:]:
        if "get_knowledge_list" not in r["url"]:
            continue
        b = r.get("body", {})
        if not isinstance(b, dict) or b.get("code") != 0:
            continue
        kl = b.get("knowledge_list", [])

        for item in kl:
            if not isinstance(item, dict):
                continue
            name  = clean(item.get("name") or item.get("title") or "")
            fid   = extract_id(item)
            pfid  = item.get("parent_folder_id", "") or ""

            if is_folder_item(item):
                if fid:
                    if name:
                        id_to_name.setdefault(fid, name)
                    id_to_parent.setdefault(fid, pfid)
                    new_folders.append(item)
            elif is_book_name(name) and name not in seen:
                seen.add(name)
                cat = get_top_category(pfid, id_to_name, id_to_parent) if pfid else "根目录"
                books.append({"书名": name, "分类": cat})

    return books, new_folders


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

        # ── 导航进入 KB ───────────────────────────────────────────────────────
        print("\n正在导航到 IMA 主页…")
        await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(3)

        print("步骤1：点击「个人知识库」…")
        ok = await try_click(page, [':text-is("个人知识库")',
                                    'text="个人知识库"', ':text("个人知识库")'])
        print(f"  {'✅' if ok else '（已展开）'}")
        await asyncio.sleep(3)

        print("步骤2：点击「共享知识库」…")
        await try_click(page, [':text-is("共享知识库")', 'text="共享知识库"',
                                ':text("共享知识库")'], timeout_ms=2000)
        await asyncio.sleep(2)

        print(f"步骤3：点击「{KB_NAME}」…")
        ok = await try_click(page, [
            f':text-is("{KB_NAME}")', f'text="{KB_NAME}"', f':text("{KB_NAME}")',
            f'[title="{KB_NAME}"]', f'span:has-text("{KB_NAME}")', f'a:has-text("{KB_NAME}")',
        ], timeout_ms=5000)

        if ok:
            print("  ✅ 已自动点击")
        else:
            print(f"  ⚠ 自动点击失败，请在 360 浏览器里手动点击左侧「{KB_NAME}」后按 Enter…")
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

        # ── 找 wikis 标签页 ────────────────────────────────────────────────────
        kb_page = page
        for p in all_pages:
            if "wikis" in p.url or "wiki" in p.url:
                kb_page = p
                break
        wikis_root_url = kb_page.url
        print(f"使用标签页：{wikis_root_url}")

        # ── 从拦截数据中找22个顶层分类 ────────────────────────────────────────
        print("\n在拦截数据中查找顶层分类…")
        top_items: list = []
        for r in api_responses:
            if "get_knowledge_list" not in r["url"]:
                continue
            b  = r.get("body", {})
            kl = b.get("knowledge_list", []) if isinstance(b, dict) and b.get("code") == 0 else []
            if len(kl) > len(top_items):
                top_items = kl

        if not top_items:
            print(f"⚠ 未找到顶层分类，请把 {DEBUG_RESP.name} 发给我分析。")
            if not using_cdp:
                await browser.close()
            return

        # 建立 ID 映射
        id_to_name:   dict[str, str] = {}
        id_to_parent: dict[str, str] = {}
        for item in top_items:
            fid  = extract_id(item)
            name = clean(item.get("name") or item.get("title") or "")
            pfid = item.get("parent_folder_id", "") or ""
            if fid and name:
                id_to_name[fid]   = name
                id_to_parent[fid] = pfid

        folder_items = [it for it in top_items if is_folder_item(it)]
        doc_items    = [it for it in top_items if not is_folder_item(it)]

        print(f"✅ 找到 {len(top_items)} 个顶层条目：{len(folder_items)} 个文件夹，{len(doc_items)} 个文档")
        for i, it in enumerate(top_items, 1):
            nm = clean(it.get("name") or it.get("title") or "?")
            tp = "📁" if is_folder_item(it) else "📄"
            print(f"  {i:2d}. {tp} {nm}")

        # ── 收集顶层直接文档 ─────────────────────────────────────────────────
        books: list = []
        seen_books: set = set()
        for it in doc_items:
            nm = clean(it.get("name") or it.get("title") or "")
            if is_book_name(nm) and nm not in seen_books:
                seen_books.add(nm)
                books.append({"书名": nm, "分类": KB_NAME})

        # ── 自动导航所有文件夹（BFS 队列）──────────────────────────────────────
        # 队列元素：(folder_item, parent_name_for_display)
        nav_queue: list = [(it, "") for it in folder_items]
        failed_folders: list = []   # (folder_name, parent_display) 自动失败的

        print(f"\n开始自动导航（共 {len(nav_queue)} 个顶层文件夹）…\n")

        while nav_queue:
            item, parent_display = nav_queue.pop(0)
            fname = clean(item.get("name") or item.get("title") or "")
            fid   = extract_id(item)
            label = f"{parent_display} → {fname}" if parent_display else fname

            print(f"  📁 {label} …", end="", flush=True)
            count_before = len(api_responses)

            ok = await navigate_to_folder(kb_page, wikis_root_url, fname)
            await asyncio.sleep(3)   # 等待 API 响应

            if ok:
                new_books, new_folders = absorb_responses(
                    api_responses, count_before, id_to_name, id_to_parent)
                for b in new_books:
                    if b["书名"] not in seen_books:
                        seen_books.add(b["书名"])
                        books.append(b)
                # 子文件夹加入队列
                for sub in new_folders:
                    sub_name = clean(sub.get("name") or sub.get("title") or "")
                    if sub_name and extract_id(sub) not in {extract_id(q[0]) for q in nav_queue}:
                        nav_queue.append((sub, fname))
                print(f" ✓ 提取 {len(new_books)} 本书，{len(new_folders)} 个子文件夹")
            else:
                failed_folders.append((fname, parent_display))
                print(f" ⚠ 自动失败")

        # ── 自动失败的文件夹：请用户手动点击 ──────────────────────────────────
        if failed_folders:
            print(f"\n{'='*60}")
            print(f"以下 {len(failed_folders)} 个文件夹未能自动导航，请在 360 浏览器里依次点击：")
            for fname, pdisplay in failed_folders:
                label = f"  {pdisplay} → {fname}" if pdisplay else f"  {fname}"
                print(label)
            print("\n全部点完后按 Enter（每个点击等 1-2 秒再点下一个）…")
            count_manual = len(api_responses)
            await async_input(">>> ")
            await asyncio.sleep(3)

            manual_books, manual_folders = absorb_responses(
                api_responses, count_manual, id_to_name, id_to_parent)
            for b in manual_books:
                if b["书名"] not in seen_books:
                    seen_books.add(b["书名"])
                    books.append(b)
            print(f"手动阶段提取 {len(manual_books)} 本书，{len(manual_folders)} 个新子文件夹")

            # 若还有更深子文件夹
            if manual_folders:
                print(f"\n{'='*60}")
                print(f"还发现 {len(manual_folders)} 个子文件夹，请继续点击：")
                for sf in manual_folders:
                    print(f"  {clean(sf.get('name') or sf.get('title') or '')}")
                print("完成后按 Enter…")
                count_deep = len(api_responses)
                await async_input(">>> ")
                await asyncio.sleep(3)
                deep_books, _ = absorb_responses(
                    api_responses, count_deep, id_to_name, id_to_parent)
                for b in deep_books:
                    if b["书名"] not in seen_books:
                        seen_books.add(b["书名"])
                        books.append(b)
                print(f"深层提取 {len(deep_books)} 本书")

        # 保存最终调试数据
        DEBUG_RESP.write_text(
            json.dumps(api_responses, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"\n共拦截到 {len(api_responses)} 个 API 响应，已保存至 {DEBUG_RESP.name}")

        if not using_cdp:
            await browser.close()

    # ── 写 CSV ────────────────────────────────────────────────────────────────
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
