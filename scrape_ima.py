#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本 v6
策略：让浏览器自己导航 + 拦截真实 API 响应
- 脚本自动点击22个顶层分类，捕获浏览器发出的 get_knowledge_list 响应
- 若有子文件夹，引导用户手动点击
- 从所有 code=0 的响应中提取书目
- 文件夹判断：media_id 以 "folder_" 开头（不再用 media_type==99）
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
    name = name.strip()
    return len(name) >= 3 and not name.startswith("http")


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
    """从 item 中提取文件夹/文档 ID。"""
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
    """
    文件夹判断：media_id 以 'folder_' 开头是最可靠的标志。
    PPT/PDF 等文档的 media_id 以 'ppt_'/'pdf_' 等开头，不算文件夹。
    只有在没有 media_id 时才参考 type/media_type。
    """
    mid = item.get("media_id", "")
    if isinstance(mid, str):
        if mid.startswith("folder_"):
            return True
        if mid and not mid.startswith("folder_"):
            return False   # 有 media_id 但不是 folder_ 开头 → 文档
    # 没有 media_id 时才参考其他字段
    if item.get("type") in (2, "folder", "dir", "directory"):
        return True
    return bool(item.get("is_folder"))


def collect_items_from_responses(
    api_responses: list,
    start_idx: int,
    cat_name: str,
    id_to_name: dict,
    id_to_parent: dict,
    folder_id_hint: str = "",
) -> tuple[list, list]:
    """
    从 api_responses[start_idx:] 提取书目和新子文件夹。
    cat_name: 当前上下文的分类名（用于没法从 req_body 推断时的兜底）。
    返回 (books_list, subfolders_list)
    subfolders_list 中每个元素为 (folder_id, folder_name, parent_cat_name)
    """
    books = []
    subfolders = []
    seen = set()

    for r in api_responses[start_idx:]:
        if "get_knowledge_list" not in r["url"]:
            continue
        b = r.get("body", {})
        if not isinstance(b, dict) or b.get("code") != 0:
            continue
        kl = b.get("knowledge_list", [])

        # 尝试从 req_body 中推断分类
        req_body = r.get("req_body", {})
        inferred_cat = cat_name
        for k, v in req_body.items():
            if isinstance(v, str) and v in id_to_name:
                inferred_cat = id_to_name[v]
                break

        for item in kl:
            if not isinstance(item, dict):
                continue
            name = clean(item.get("name") or item.get("title") or "")
            fid  = extract_id(item)

            if is_folder_item(item):
                if fid and name and fid not in id_to_name:
                    id_to_name[fid] = name
                    id_to_parent[fid] = folder_id_hint or ""
                    subfolders.append((fid, name, inferred_cat))
            elif is_book_name(name) and name not in seen:
                seen.add(name)
                books.append({"书名": name, "分类": inferred_cat})

    return books, subfolders


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
            print(f"  请手动点击左侧「{KB_NAME}」后按 Enter…")
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
        print(f"使用标签页：{kb_page.url}")

        # ── 从拦截数据中找22个顶层分类 ────────────────────────────────────────
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

        if not top_items:
            print(f"⚠ 未找到顶层分类，请把 {DEBUG_RESP.name} 发给我分析。")
            if not using_cdp:
                await browser.close()
            return

        print(f"✅ 找到 {len(top_items)} 个顶层分类")
        print(f"   对应请求体：{json.dumps(top_req_body, ensure_ascii=False)}")

        # 建立 ID → 名称 / ID → 父级 ID 映射
        id_to_name:   dict[str, str] = {}
        id_to_parent: dict[str, str] = {}
        for item in top_items:
            fid   = extract_id(item)
            fname = clean(item.get("name") or item.get("title") or "")
            pfid  = item.get("parent_folder_id", "") or ""
            if fid and fname:
                id_to_name[fid]   = fname
                id_to_parent[fid] = pfid

        print(f"\n所有 {len(top_items)} 个顶层条目：")
        for i, item in enumerate(top_items, 1):
            nm  = clean(item.get("name") or item.get("title") or "?")
            cid = extract_id(item)
            tp  = "📁" if is_folder_item(item) else "📄"
            print(f"  {i:2d}. {tp} {nm}  (id={cid[:30]}{'...' if len(cid)>30 else ''})")

        # ── 收集直接在顶层的文档（非文件夹）─────────────────────────────────
        books: list = []
        seen_books:  set = set()
        for item in top_items:
            name = clean(item.get("name") or item.get("title") or "")
            if not is_folder_item(item) and is_book_name(name) and name not in seen_books:
                seen_books.add(name)
                books.append({"书名": name, "分类": KB_NAME})

        # ── 自动点击每个顶层文件夹 ────────────────────────────────────────────
        folder_items   = [item for item in top_items if is_folder_item(item)]
        wikis_root_url = kb_page.url          # 记住 wikis 根 URL，用于每次导航重置
        url_pattern    = None                 # 发现后填入，如 "https://…/wikis?id=FOLDER_ID"
        print(f"\n共 {len(folder_items)} 个文件夹，开始自动点击…\n")
        print(f"wikis 根 URL：{wikis_root_url}\n")

        pending_subfolders: list = []   # (folder_id, name, parent_cat_name)

        for item in folder_items:
            cat_name = clean(item.get("name") or item.get("title") or "")
            cat_id   = extract_id(item)
            if not cat_name:
                continue

            count_before = len(api_responses)
            print(f"📁 {cat_name}  …", end="", flush=True)

            navigated = False

            # ① 若已发现 URL 规律，直接跳转
            if url_pattern and cat_id:
                folder_url = url_pattern.replace("FOLDER_ID", cat_id)
                try:
                    await kb_page.goto(folder_url, wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(2)
                    navigated = True
                except Exception:
                    pass

            # ② 否则：先回到 wikis 根，再点击文件夹名
            if not navigated:
                if kb_page.url.rstrip("/") != wikis_root_url.rstrip("/"):
                    try:
                        await kb_page.goto(wikis_root_url, wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(2)
                    except Exception:
                        pass

                clicked = await try_click(kb_page, [
                    f':text-is("{cat_name}")',
                    f'span:has-text("{cat_name}")',
                    f'li:has-text("{cat_name}")',
                    f'div:has-text("{cat_name}")',
                    f'[title="{cat_name}"]',
                ], timeout_ms=3000)
                navigated = clicked

            # 等待 API 响应到达
            await asyncio.sleep(3)

            # ③ 若本次找到了 URL 规律（cat_id 出现在 URL 里），记录下来
            if url_pattern is None and cat_id:
                cur_url = kb_page.url
                if cat_id in cur_url:
                    url_pattern = cur_url.replace(cat_id, "FOLDER_ID")
                    print(f"\n   ✅ URL模式：{url_pattern}", end="")

            # 收集本次导航产生的书目和子文件夹
            new_books, new_subs = collect_items_from_responses(
                api_responses, count_before, cat_name,
                id_to_name, id_to_parent, folder_id_hint=cat_id
            )

            for b in new_books:
                if b["书名"] not in seen_books:
                    seen_books.add(b["书名"])
                    books.append(b)

            pending_subfolders.extend(new_subs)

            status = "✓ 导航成功" if navigated else "⚠ 未能导航"
            print(f" {status}，捕获到 {len(new_books)} 本书，{len(new_subs)} 个子文件夹")

        # ── 处理子文件夹（先尝试自动导航，再提示手动）─────────────────────────
        if pending_subfolders:
            print(f"\n发现 {len(pending_subfolders)} 个子文件夹，尝试自动导航…\n")
            for fid, fname, pname in pending_subfolders:
                id_to_name.setdefault(fid, fname)
                id_to_parent.setdefault(fid, "")

            count_before_subs = len(api_responses)
            auto_sub_ok = 0

            for fid, fname, pname in pending_subfolders:
                print(f"  📁 {pname} → {fname}  …", end="", flush=True)
                sub_navigated = False

                if url_pattern:
                    sub_url = url_pattern.replace("FOLDER_ID", fid)
                    try:
                        await kb_page.goto(sub_url, wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(2)
                        sub_navigated = True
                        auto_sub_ok += 1
                    except Exception:
                        pass

                if not sub_navigated:
                    # 回到根，再点击
                    try:
                        await kb_page.goto(wikis_root_url, wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(2)
                    except Exception:
                        pass
                    sub_navigated = await try_click(kb_page, [
                        f':text-is("{fname}")',
                        f'span:has-text("{fname}")',
                        f'[title="{fname}"]',
                    ], timeout_ms=3000)
                    if sub_navigated:
                        await asyncio.sleep(2)

                print(f" {'✓' if sub_navigated else '⚠'}")

            # 收集子文件夹书目
            sub_books, more_subs = collect_items_from_responses(
                api_responses, count_before_subs, "未知分类",
                id_to_name, id_to_parent
            )
            for b in sub_books:
                if b["书名"] not in seen_books:
                    seen_books.add(b["书名"])
                    books.append(b)
            print(f"\n自动导航子文件夹：{auto_sub_ok}/{len(pending_subfolders)} 个，提取 {len(sub_books)} 本书")

            # 若仍有未处理的更深层或自动失败的，提示手动
            failed_subs = [x for x in pending_subfolders
                           if x[0] not in {extract_id(b) for b in sub_books}]
            all_remaining = more_subs + [x for x in pending_subfolders if auto_sub_ok < len(pending_subfolders)]

            if more_subs or auto_sub_ok < len(pending_subfolders):
                print(f"\n{'='*60}")
                if more_subs:
                    print(f"还发现 {len(more_subs)} 个更深层子文件夹：")
                    for fid, fname, pname in more_subs:
                        print(f"  {pname} → {fname}")
                print("请在浏览器里手动点击以上所有未完成的子文件夹，完成后按 Enter…")
                count_before_deep = len(api_responses)
                await async_input(">>> ")
                await asyncio.sleep(2)
                deep_books, _ = collect_items_from_responses(
                    api_responses, count_before_deep, "未知分类",
                    id_to_name, id_to_parent
                )
                for b in deep_books:
                    if b["书名"] not in seen_books:
                        seen_books.add(b["书名"])
                        books.append(b)
        else:
            print("\n未发现子文件夹。")

        # 保存最终调试数据
        DEBUG_RESP.write_text(
            json.dumps(api_responses, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

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
