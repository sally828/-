#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本 v8
策略：
  1. 用户手动点击22个顶层文件夹（每个等1-2秒）→ 脚本拦截所有响应
  2. 从成功响应里自动发现 API 请求体格式和请求头
  3. 用 Playwright APIRequestContext 直接调用 API（共享浏览器 cookies）
     自动 BFS 遍历全部子文件夹，无需人工再点击
  4. 每个 item 用 parent_folder_id 反推顶层分类
"""

import asyncio
import csv
import json
import re
from collections import Counter, deque
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
            # 同时捕获请求头，用于后续复现
            try:
                req_headers = dict(response.request.headers)
            except Exception:
                req_headers = {}
            api_responses.append({
                "url":         response.url,
                "req_body":    req_body,
                "req_headers": req_headers,
                "body":        rb,
            })
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
    mid = item.get("media_id", "")
    if isinstance(mid, str):
        if mid.startswith("folder_"):
            return True
        if mid:
            return False
    return item.get("type") in (2, "folder", "dir", "directory") or bool(item.get("is_folder"))


def get_top_category(folder_id: str, id_to_name: dict, id_to_parent: dict) -> str:
    cur = folder_id
    visited: set = set()
    while cur and cur not in visited:
        visited.add(cur)
        parent = id_to_parent.get(cur, "")
        if not parent or parent == KB_FOLDER_ID:
            return id_to_name.get(cur, "未知分类")
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


# ── API 直接调用（用浏览器 cookies + 正确 headers）──────────────────────────

async def direct_api_call(kb_page, body: dict,
                          extra_headers: dict | None = None) -> dict:
    """
    通过 Playwright APIRequestContext 直接调用 API。
    共享浏览器 context 的 cookies，可设置任意请求头。
    """
    headers = {
        "Content-Type":  "application/json",
        "Accept":        "application/json, */*",
        "Origin":        "https://ima.qq.com",
        "Referer":       "https://ima.qq.com/wikis",
    }
    if extra_headers:
        headers.update(extra_headers)
    try:
        resp = await kb_page.request.post(
            MEMBER_API,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
        )
        return await resp.json()
    except Exception as e:
        return {"code": -1, "error": str(e)}


def discover_template(api_responses: list) -> tuple[dict | None, dict]:
    """
    从已拦截的成功响应里发现：
    - body_template: 请求体模板，"__ID__" 是 folder_id 占位符
    - extra_headers: 浏览器额外发送的请求头（排除 content-type 等通用头）
    """
    SKIP_HEADERS = {
        "content-type", "content-length", "accept", "accept-encoding",
        "accept-language", "connection", "host", "origin", "referer",
        "user-agent", "cookie",
    }
    for r in api_responses:
        if "get_knowledge_list" not in r["url"]:
            continue
        b  = r.get("body", {})
        kl = b.get("knowledge_list", []) if isinstance(b, dict) and b.get("code") == 0 else []
        if not kl:
            continue
        req = r.get("req_body", {})
        if not req:
            continue

        # 找请求体里含有 folder_ 前缀的字段
        folder_key = None
        static = {}
        for k, v in req.items():
            if isinstance(v, str) and v.startswith("folder_"):
                folder_key = k
            else:
                static[k] = v

        if folder_key:
            tpl = {folder_key: "__ID__"}
            tpl.update(static)
            extra = {k: v for k, v in r.get("req_headers", {}).items()
                     if k.lower() not in SKIP_HEADERS}
            return tpl, extra

    return None, {}


def make_body(template: dict, folder_id: str, cursor: str = "") -> dict:
    body = {k: (folder_id if v == "__ID__" else v) for k, v in template.items()}
    body["cursor"] = cursor
    return body


def absorb_items(kl: list,
                 id_to_name: dict, id_to_parent: dict,
                 seen_books: set) -> tuple[list, list]:
    """处理一批 knowledge_list items，返回 (books, new_folder_items)。"""
    books:      list = []
    new_folders: list = []
    for item in kl:
        if not isinstance(item, dict):
            continue
        name = clean(item.get("name") or item.get("title") or "")
        fid  = extract_id(item)
        pfid = item.get("parent_folder_id", "") or ""
        if is_folder_item(item):
            if fid:
                if name:
                    id_to_name.setdefault(fid, name)
                id_to_parent.setdefault(fid, pfid)
                new_folders.append(item)
        elif is_book_name(name) and name not in seen_books:
            seen_books.add(name)
            cat = get_top_category(pfid, id_to_name, id_to_parent) if pfid else KB_NAME
            books.append({"书名": name, "分类": cat})
    return books, new_folders


async def bfs_fetch(kb_page, root_folders: list,
                    id_to_name: dict, id_to_parent: dict,
                    seen_books: set, template: dict,
                    extra_headers: dict) -> list:
    """
    BFS 遍历所有子文件夹，用直接 API 调用提取书目。
    返回提取到的书目列表。
    """
    books: list = []
    queue: deque = deque(root_folders)
    total = len(queue)
    done  = 0

    print(f"\n开始 BFS 直接 API 提取（队列 {total} 个文件夹）…")

    while queue:
        item = queue.popleft()
        fid  = extract_id(item)
        fname = clean(item.get("name") or item.get("title") or "")
        if not fid:
            continue

        done += 1
        if done % 20 == 0 or done <= 5:
            print(f"  [{done}/{total}] 📁 {fname} …", end="", flush=True)

        cursor = ""
        page_n = 0
        folder_books = 0
        folder_subs  = 0

        while True:
            body = make_body(template, fid, cursor)
            data = await direct_api_call(kb_page, body, extra_headers)
            code = data.get("code", -1)
            kl   = data.get("knowledge_list", [])
            page_n += 1

            if code != 0:
                if done % 20 == 0 or done <= 5:
                    print(f" ⚠ code={code}")
                break

            new_books, new_folders = absorb_items(
                kl, id_to_name, id_to_parent, seen_books)
            books.extend(new_books)
            folder_books += len(new_books)
            folder_subs  += len(new_folders)

            for sf in new_folders:
                queue.append(sf)
                total += 1

            next_cur = data.get("next_cursor", "")
            if data.get("is_end", True) or not kl or not next_cur or next_cur == cursor:
                break
            cursor = next_cur
            await asyncio.sleep(0.2)

        if done % 20 == 0 or done <= 5:
            print(f" ✓ {folder_books}本书, {folder_subs}子文件夹")
        await asyncio.sleep(0.15)

    print(f"\nBFS 完成：共处理 {done} 个文件夹，提取 {len(books)} 本书")
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
            print(f"  ⚠ 请手动点击左侧「{KB_NAME}」后按 Enter…")
            await async_input("  >>> ")

        print("\n等待 10 秒，让知识库页面加载完成…")
        await asyncio.sleep(10)

        all_pages = ctx.pages if ctx else [page]
        kb_page = page
        for p in all_pages:
            if "wikis" in p.url or "wiki" in p.url:
                kb_page = p
                break
        print(f"\n使用标签页：{kb_page.url}")
        print(f"共拦截到 {len(api_responses)} 个 API 响应")

        # ── 获取22个顶层分类 ──────────────────────────────────────────────────
        top_items: list = []
        for r in api_responses:
            if "get_knowledge_list" not in r["url"]:
                continue
            b  = r.get("body", {})
            kl = b.get("knowledge_list", []) if isinstance(b, dict) and b.get("code") == 0 else []
            if len(kl) > len(top_items):
                top_items = kl

        if not top_items:
            print(f"⚠ 未找到顶层分类。请把 {DEBUG_RESP.name} 发给我分析。")
            if not using_cdp:
                await browser.close()
            return

        id_to_name:   dict[str, str] = {}
        id_to_parent: dict[str, str] = {}
        for it in top_items:
            fid  = extract_id(it)
            name = clean(it.get("name") or it.get("title") or "")
            pfid = it.get("parent_folder_id", "") or ""
            if fid and name:
                id_to_name[fid]   = name
                id_to_parent[fid] = pfid

        folder_items = [it for it in top_items if is_folder_item(it)]
        doc_items    = [it for it in top_items if not is_folder_item(it)]
        print(f"✅ 顶层：{len(folder_items)} 个文件夹 + {len(doc_items)} 个文档")

        seen_books: set  = set()
        books:      list = []

        # 顶层直接文档
        for it in doc_items:
            nm = clean(it.get("name") or it.get("title") or "")
            if is_book_name(nm) and nm not in seen_books:
                seen_books.add(nm)
                books.append({"书名": nm, "分类": KB_NAME})

        # ── 第一阶段：请用户点击22个顶层文件夹（一次性操作）────────────────────
        print(f"\n{'='*65}")
        print("【第一步】请在 360 浏览器里依次点击以下 22 个顶层文件夹：")
        print("（每点一个等 1-2 秒，让页面加载完再点下一个）\n")
        for i, it in enumerate(folder_items, 1):
            nm = clean(it.get("name") or it.get("title") or "")
            print(f"  {i:2d}. {nm}")
        print(f"\n全部点完后按 Enter…")
        print(f"{'='*65}")
        count_phase1 = len(api_responses)
        await async_input(">>> ")
        await asyncio.sleep(3)

        # 处理第一阶段响应
        phase1_books:   list = []
        phase1_folders: list = []
        for r in api_responses[count_phase1:]:
            if "get_knowledge_list" not in r["url"]:
                continue
            b  = r.get("body", {})
            kl = b.get("knowledge_list", []) if isinstance(b, dict) and b.get("code") == 0 else []
            if not kl:
                continue
            nb, nf = absorb_items(kl, id_to_name, id_to_parent, seen_books)
            phase1_books.extend(nb)
            phase1_folders.extend(nf)

        books.extend(phase1_books)
        print(f"\n第一阶段拦截：{len(phase1_books)} 本书，{len(phase1_folders)} 个子文件夹")

        # ── 发现 API 参数模板 ────────────────────────────────────────────────
        template, extra_headers = discover_template(api_responses[count_phase1:])

        if template:
            print(f"\n✅ 发现 API 参数模板：{template}")
            print(f"   额外请求头：{list(extra_headers.keys()) or '（无）'}")
        else:
            # 尝试从顶层响应里找模板（空 body）
            print("\n⚠ 未从子文件夹响应中发现含 folder_ 的请求体")
            print("  尝试从拦截到的所有响应里推断…")
            template, extra_headers = discover_template(api_responses)
            if template:
                print(f"  ✅ 找到：{template}")
            else:
                print("  ❌ 仍未找到模板，将尝试常见参数组合…")
                # 用第一个成功的子文件夹 ID 来探测
                if phase1_folders:
                    test_fid = extract_id(phase1_folders[0])
                    for try_body in [
                        {"folder_id": test_fid, "cursor": ""},
                        {"knowledge_base_id": KB_FOLDER_ID, "folder_id": test_fid, "cursor": ""},
                        {"knowledge_base_id": test_fid, "cursor": ""},
                    ]:
                        d = await direct_api_call(kb_page, try_body, extra_headers)
                        code = d.get("code", -1)
                        kl   = d.get("knowledge_list", [])
                        print(f"  {try_body} → code={code}, items={len(kl)}")
                        if code == 0:
                            # Build template
                            for k, v in try_body.items():
                                if v == test_fid:
                                    template = {k: "__ID__"}
                                    for k2, v2 in try_body.items():
                                        if k2 != k and k2 != "cursor":
                                            template[k2] = v2
                                    break
                            if template:
                                print(f"  ✅ 探测成功：{template}")
                                break
                        await asyncio.sleep(0.3)

        # ── 第二阶段：BFS 自动提取所有子文件夹 ──────────────────────────────
        if template and phase1_folders:
            bfs_books = await bfs_fetch(
                kb_page, phase1_folders, id_to_name, id_to_parent,
                seen_books, template, extra_headers)
            books.extend(bfs_books)
        elif phase1_folders:
            print(f"\n⚠ 未能发现可用的 API 参数，无法自动提取 {len(phase1_folders)} 个子文件夹")
            print(f"  请把 {DEBUG_RESP.name} 发给我分析。")

        # 保存调试数据
        DEBUG_RESP.write_text(
            json.dumps(api_responses, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"\n共拦截 {len(api_responses)} 个响应，已保存至 {DEBUG_RESP.name}")

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
