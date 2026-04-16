#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本 v9
策略：
  1. 全自动点击 22 个顶层文件夹（Playwright 自动操作，无需手动）
  2. 从拦截到的浏览器真实请求中发现 API 参数模板
  3. 优先用 page.evaluate() 执行 fetch()（走浏览器自己的网络栈）
     → BFS 自动遍历所有子文件夹
  4. 若 fetch 失败(code≠0)，回退到全自动点击导航
     → BFS 路径点击，同样遍历到最末级
"""

import asyncio
import csv
import json
import re
from collections import Counter, deque
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, Page

# ── 配置 ──────────────────────────────────────────────────────────────────────
KB_NAME      = "追梦人的财经图书馆"
KB_FOLDER_ID = "7374371035301653"
MEMBER_API   = "https://ima.qq.com/cgi-bin/knowledge_tab_reader/get_knowledge_list"
PROXY        = "http://127.0.0.1:10808"
CDP_URL      = "http://localhost:9222"
OUTPUT_CSV   = Path(__file__).parent / "ima_booklist.csv"
DEBUG_JSON   = Path(__file__).parent / "ima_debug.json"
CLICK_DELAY  = 2.5   # 秒，每次点击后等待 API 响应
NAV_DELAY    = 2.0   # 秒，goto 后等待页面渲染
# ─────────────────────────────────────────────────────────────────────────────


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def is_book_name(name: str) -> bool:
    s = name.strip()
    return len(s) >= 3 and not s.startswith("http")


async def async_input(prompt: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)


# ── 响应拦截 ──────────────────────────────────────────────────────────────────

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


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def extract_id(item: dict) -> str:
    for field in ["media_id", "folder_id", "knowledge_base_id",
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


def absorb_items(kl: list, id_to_name: dict, id_to_parent: dict,
                 seen_books: set) -> tuple[list, list]:
    books: list       = []
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


def req_body_has_id(req_body: dict, folder_id: str) -> bool:
    """检查请求体里是否含有该文件夹 ID（任意字段值匹配）。"""
    return any(str(v) == folder_id for v in req_body.values() if v)


def discover_template(api_responses: list) -> tuple[dict | None, dict]:
    SKIP = {"content-type", "content-length", "accept", "accept-encoding",
            "accept-language", "connection", "host", "origin", "referer",
            "user-agent", "cookie"}
    for r in reversed(api_responses):
        if "get_knowledge_list" not in r["url"]:
            continue
        b  = r.get("body", {})
        kl = b.get("knowledge_list", []) if isinstance(b, dict) and b.get("code") == 0 else []
        if not kl:
            continue
        req = r.get("req_body", {})
        if not req:
            continue
        folder_key = None
        static: dict = {}
        for k, v in req.items():
            if isinstance(v, str) and v.startswith("folder_"):
                folder_key = k
            else:
                static[k] = v
        if folder_key:
            tpl = {folder_key: "__ID__"}
            tpl.update(static)
            extra = {k: v for k, v in r.get("req_headers", {}).items()
                     if k.lower() not in SKIP}
            return tpl, extra
    return None, {}


def make_body(template: dict, folder_id: str, cursor: str = "") -> dict:
    body = {k: (folder_id if v == "__ID__" else v) for k, v in template.items()}
    body["cursor"] = cursor
    return body


# ── 浏览器内 fetch（走浏览器真实网络栈）─────────────────────────────────────

async def js_fetch(page: Page, body: dict) -> dict:
    """在浏览器 JS 上下文里执行 fetch()，自动带上 cookies 和正确的 Sec-Fetch-* 头。"""
    try:
        result = await page.evaluate(
            """async ([url, bodyStr]) => {
                try {
                    const r = await fetch(url, {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'Content-Type': 'application/json',
                            'Accept': 'application/json, */*',
                        },
                        body: bodyStr
                    });
                    return await r.json();
                } catch(e) {
                    return {code: -999, error: e.toString()};
                }
            }""",
            [MEMBER_API, json.dumps(body, ensure_ascii=False)]
        )
        return result if isinstance(result, dict) else {"code": -2}
    except Exception as e:
        return {"code": -1, "error": str(e)}


# ── 浏览器点击工具 ────────────────────────────────────────────────────────────

async def try_click_name(page: Page, name: str, timeout_ms: int = 5000) -> bool:
    safe = name.replace("\\", "\\\\").replace('"', '\\"')
    for sel in [
        f'[title="{safe}"]',
        f':text-is("{safe}")',
        f'span:has-text("{safe}")',
        f'div:has-text("{safe}")',
        f'a:has-text("{safe}")',
        f'li:has-text("{safe}")',
    ]:
        try:
            loc = page.locator(sel).first
            await loc.scroll_into_view_if_needed(timeout=timeout_ms)
            await loc.click(timeout=timeout_ms)
            return True
        except Exception:
            pass
    return False


async def navigate_path(page: Page, path: list[str], root_url: str) -> bool:
    """从 root_url 出发，依次点击路径里每个文件夹名，导航到目标层级。"""
    await page.goto(root_url, wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(NAV_DELAY)
    for folder_name in path:
        found = await try_click_name(page, folder_name)
        if not found:
            print(f"    ⚠ 找不到: {folder_name}")
            return False
        await asyncio.sleep(CLICK_DELAY)
    return True


# ── Phase 1：全自动点击 22 个顶层文件夹 ─────────────────────────────────────

async def auto_click_top_folders(
    page: Page, folder_items: list,
    id_to_name: dict, id_to_parent: dict,
    api_responses: list, root_url: str, seen_books: set
) -> tuple[list, list]:
    books: list       = []
    sub_folders: list = []
    total = len(folder_items)
    print(f"\n【Phase 1】全自动点击 {total} 个顶层文件夹…")
    for i, item in enumerate(folder_items, 1):
        name = clean(item.get("name") or item.get("title") or "")
        fid  = extract_id(item)
        if not name or not fid:
            continue
        print(f"  [{i:2d}/{total}] {name}… ", end="", flush=True)
        count_before = len(api_responses)

        await page.goto(root_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(NAV_DELAY)
        found = await try_click_name(page, name)
        if not found:
            print("⚠ 元素未找到")
            continue
        await asyncio.sleep(CLICK_DELAY)

        fb, ff = 0, 0
        for r in api_responses[count_before:]:
            if "get_knowledge_list" not in r["url"]:
                continue
            b  = r.get("body", {})
            kl = b.get("knowledge_list", []) if isinstance(b, dict) and b.get("code") == 0 else []
            if not kl:
                continue
            nb, nf = absorb_items(kl, id_to_name, id_to_parent, seen_books)
            books.extend(nb)
            sub_folders.extend(nf)
            fb += len(nb); ff += len(nf)
        print(f"✓ {fb} 本书  {ff} 子文件夹")
    return books, sub_folders


# ── Phase 2A：BFS + 浏览器 JS fetch ─────────────────────────────────────────

async def bfs_js_fetch(
    page: Page, root_folders: list,
    id_to_name: dict, id_to_parent: dict,
    seen_books: set, template: dict
) -> tuple[list, bool]:
    """返回 (books, success)。连续 5 次 code≠0 则放弃，返回 success=False。"""
    books: list     = []
    visited: set    = set()
    queue: deque    = deque(root_folders)
    total           = len(queue)
    done            = 0
    fail_streak     = 0
    MAX_FAIL_STREAK = 5

    print(f"\n【Phase 2A】JS fetch BFS，队列 {total} 个文件夹…")

    while queue:
        item  = queue.popleft()
        fid   = extract_id(item)
        fname = clean(item.get("name") or item.get("title") or "")
        if not fid or fid in visited:
            continue
        visited.add(fid)
        done += 1

        cursor    = ""
        folder_ok = False
        while True:
            body = make_body(template, fid, cursor)
            data = await js_fetch(page, body)
            code = data.get("code", -1)
            kl   = data.get("knowledge_list", [])

            if code != 0:
                fail_streak += 1
                if done <= 5 or done % 50 == 0:
                    print(f"  [{done}/{total}] ⚠ {fname}: code={code}")
                if fail_streak >= MAX_FAIL_STREAK and not folder_ok:
                    print(f"\n  连续 {MAX_FAIL_STREAK} 次失败，切换自动点击模式")
                    return books, False
                break

            fail_streak = 0
            folder_ok   = True
            nb, nf      = absorb_items(kl, id_to_name, id_to_parent, seen_books)
            books.extend(nb)
            for sf in nf:
                if extract_id(sf) not in visited:
                    queue.append(sf)
                    total += 1

            if done <= 5 or done % 100 == 0:
                print(f"  [{done}/{total}] ✓ {fname}: {len(nb)} 本书  {len(nf)} 子文件夹")

            next_cur = data.get("next_cursor", "")
            if data.get("is_end", True) or not kl or not next_cur or next_cur == cursor:
                break
            cursor = next_cur
            await asyncio.sleep(0.2)

        await asyncio.sleep(0.1)

    print(f"\n  JS fetch BFS 完成：{done} 个文件夹，累计 {len(books)} 本书")
    return books, True


# ── Phase 2B：BFS + 全自动点击导航（兜底，必定有效）─────────────────────────

async def bfs_auto_click(
    page: Page, root_folders: list,
    id_to_name: dict, id_to_parent: dict,
    seen_books: set, api_responses: list, root_url: str
) -> list:
    """
    从 root_url 出发，按路径点击到每个文件夹，拦截浏览器真实 API 响应。
    队列元素格式：(item, path_list)
    path_list 是从顶层到当前文件夹的名称列表。
    """
    books: list  = []
    visited: set = set()

    # 构建初始队列：把每个 root_folder 的路径追溯到顶层
    queue: deque = deque()
    for item in root_folders:
        fid   = extract_id(item)
        fname = clean(item.get("name") or item.get("title") or "")
        pfid  = item.get("parent_folder_id", "")
        pname = id_to_name.get(pfid, "")
        path  = [pname, fname] if pname else [fname]
        queue.append((item, path))

    total = len(queue)
    done  = 0
    print(f"\n【Phase 2B】自动点击 BFS，队列 {total} 个文件夹")
    print("  （每个文件夹约 5-8 秒，全部完成后自动输出 CSV）\n")

    while queue:
        item, path = queue.popleft()
        fid        = extract_id(item)
        if not fid or fid in visited:
            continue
        visited.add(fid)
        done += 1

        if done % 20 == 0 or done <= 5:
            print(f"  [{done}/{total}] 导航: {' ▶ '.join(path)}")

        count_before = len(api_responses)
        ok = await navigate_path(page, path, root_url)
        if not ok:
            continue
        # 等待 API 响应稳定
        await asyncio.sleep(1.0)

        nb_total, nf_total = 0, 0
        for r in api_responses[count_before:]:
            if "get_knowledge_list" not in r["url"]:
                continue
            req_b = r.get("req_body", {})
            # 只处理目标文件夹的响应（req_body 里含有该 folder_id）
            if not req_body_has_id(req_b, fid):
                continue
            b  = r.get("body", {})
            kl = b.get("knowledge_list", []) if isinstance(b, dict) and b.get("code") == 0 else []
            if not kl:
                continue
            nb, nf = absorb_items(kl, id_to_name, id_to_parent, seen_books)
            books.extend(nb)
            nb_total += len(nb)
            for sf in nf:
                sf_fid  = extract_id(sf)
                sf_name = clean(sf.get("name") or sf.get("title") or "")
                if sf_fid and sf_fid not in visited and sf_name:
                    queue.append((sf, path + [sf_name]))
                    total += 1
            nf_total += len(nf)

        if done % 20 == 0 or done <= 5:
            print(f"    → {nb_total} 本书  {nf_total} 新子文件夹")

    print(f"\n  自动点击 BFS 完成：{done} 个文件夹，累计 {len(books)} 本书")
    return books


# ── 主流程 ────────────────────────────────────────────────────────────────────

async def main():
    api_responses: list = []

    async with async_playwright() as pw:
        using_cdp = False
        ctx: BrowserContext | None = None

        # 连接 360 浏览器
        try:
            browser  = await pw.chromium.connect_over_cdp(CDP_URL)
            ctx      = browser.contexts[0] if browser.contexts else None
            page     = None
            if ctx:
                for p in ctx.pages:
                    if "ima.qq.com" in p.url:
                        page = p
                        break
                if page is None:
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            else:
                page = await browser.new_page()
            using_cdp = True
            print("✅ 已连接到 360浏览器（CDP）")
        except Exception:
            print("未检测到 360浏览器，启动内置 Chromium…")
            browser = await pw.chromium.launch(headless=False)
            ctx      = await browser.new_context()
            page     = await ctx.new_page()

        # 注册拦截器
        if ctx:
            for p in ctx.pages:
                register_on_page(p, api_responses)
            ctx.on("page", lambda np: register_on_page(np, api_responses))
        else:
            register_on_page(page, api_responses)

        if not using_cdp:
            await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40_000)
            print("请登录后按 Enter")
            await async_input(">>> ")

        # ── 导航进入知识库 ────────────────────────────────────────────────────
        print("\n导航到 IMA 主页…")
        await page.goto("https://ima.qq.com", wait_until="domcontentloaded", timeout=40_000)
        await asyncio.sleep(3)

        print("点击「个人知识库」…")
        await try_click_name(page, "个人知识库")
        await asyncio.sleep(3)

        print("点击「共享知识库」…")
        await try_click_name(page, "共享知识库")
        await asyncio.sleep(2)

        print(f"点击「{KB_NAME}」…")
        ok = await try_click_name(page, KB_NAME, timeout_ms=6000)
        if not ok:
            print(f"  ⚠ 自动点击失败，请手动点击「{KB_NAME}」后按 Enter")
            await async_input("  >>> ")

        print("等待知识库页面加载（10 秒）…")
        await asyncio.sleep(10)

        # 找到 wikis 标签页
        all_pages = ctx.pages if ctx else [page]
        kb_page   = page
        for p in all_pages:
            if "wikis" in p.url or "wiki" in p.url:
                kb_page = p
                break
        root_url = kb_page.url
        print(f"标签页 URL：{root_url}")
        print(f"已拦截 {len(api_responses)} 个 API 响应")

        # ── 获取 22 个顶层分类 ────────────────────────────────────────────────
        top_items: list = []
        for r in api_responses:
            if "get_knowledge_list" not in r["url"]:
                continue
            b  = r.get("body", {})
            kl = b.get("knowledge_list", []) if isinstance(b, dict) and b.get("code") == 0 else []
            if len(kl) > len(top_items):
                top_items = kl

        if not top_items:
            print("⚠ 未找到顶层列表，请检查是否已进入知识库页面。")
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
        print(f"✅ 顶层：{len(folder_items)} 个文件夹  {len(doc_items)} 个文档")

        seen_books: set = set()
        books: list     = []

        # 顶层直接文档
        for it in doc_items:
            nm = clean(it.get("name") or it.get("title") or "")
            if is_book_name(nm) and nm not in seen_books:
                seen_books.add(nm)
                books.append({"书名": nm, "分类": KB_NAME})

        # ── Phase 1：全自动点击 22 个顶层文件夹 ─────────────────────────────
        p1_books, p1_folders = await auto_click_top_folders(
            kb_page, folder_items, id_to_name, id_to_parent,
            api_responses, root_url, seen_books
        )
        books.extend(p1_books)
        print(f"\nPhase 1 完成：{len(p1_books)} 本书  {len(p1_folders)} 个子文件夹")

        # ── Phase 2：处理所有子文件夹 ────────────────────────────────────────
        if p1_folders:
            template, _ = discover_template(api_responses)

            if template:
                print(f"\n✅ 发现 API 参数模板：{template}")
                js_books, js_ok = await bfs_js_fetch(
                    kb_page, p1_folders, id_to_name, id_to_parent, seen_books, template
                )
                books.extend(js_books)

                if not js_ok:
                    # JS fetch 失败，回退到全自动点击
                    click_books = await bfs_auto_click(
                        kb_page, p1_folders, id_to_name, id_to_parent,
                        seen_books, api_responses, root_url
                    )
                    books.extend(click_books)
            else:
                # 未找到模板，直接走自动点击
                print("\n⚠ 未找到 API 参数模板，直接使用自动点击模式")
                click_books = await bfs_auto_click(
                    kb_page, p1_folders, id_to_name, id_to_parent,
                    seen_books, api_responses, root_url
                )
                books.extend(click_books)

        # 保存调试数据（最后 100 条响应）
        DEBUG_JSON.write_text(
            json.dumps(api_responses[-100:], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"\n调试数据已保存至 {DEBUG_JSON.name}（最后 100 条）")

        if not using_cdp:
            await browser.close()

    # ── 写 CSV ────────────────────────────────────────────────────────────────
    if not books:
        print("\n⚠ 未提取到任何书目，请把 ima_debug.json 发给我分析。")
        return

    books.sort(key=lambda b: (b["分类"], b["书名"]))
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["序号", "分类", "原书名", "状态", "找到书名"])
        writer.writeheader()
        for i, b in enumerate(books, 1):
            writer.writerow({
                "序号": i, "分类": b["分类"], "原书名": b["书名"],
                "状态": "", "找到书名": ""
            })

    print(f"\n✅ 书单已保存：{OUTPUT_CSV}，共 {len(books)} 条\n")
    counts = Counter(b["分类"] for b in books)
    for cat, cnt in sorted(counts.items()):
        print(f"   {cat}：{cnt} 本")


if __name__ == "__main__":
    asyncio.run(main())
