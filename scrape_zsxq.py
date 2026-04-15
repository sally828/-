#!/usr/bin/env python3
"""
知识星球书单提取脚本
通过拦截 API 响应，提取「追梦人的财经图书馆」的完整书单，保留分类结构。
输出格式与 books_input.csv 兼容，可直接供 search_anna.py 使用。

运行：python scrape_zsxq.py
操作：打开浏览器登录 → 逐一点击每个分类文件夹并滚到底 → 回终端按 Enter
"""

import asyncio
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from playwright.async_api import async_playwright

# ── 配置 ──────────────────────────────────────────────────────────────────────
PROXY      = "http://127.0.0.1:10808"   # 不需要代理改成 None
OUTPUT_CSV = Path(__file__).parent / "ima_booklist.csv"
DEBUG_JSON = Path(__file__).parent / "zsxq_raw.json"   # 调试用原始数据
# ─────────────────────────────────────────────────────────────────────────────

ZSXQ_API = "api.zsxq.com"


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def first_line(text: str, max_len: int = 80) -> str:
    """取文字的第一行，去掉 HTML/空白"""
    text = strip_html(text)
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) >= 3:
            return line[:max_len]
    return ""


def parse_topic(topic: dict, category: str) -> dict | None:
    """
    从单条 topic JSON 中提取书名。
    知识星球 topic 结构：
      topic["talk"]["files"]  → 文件附件列表，每项有 "name"
      topic["talk"]["text"]   → 帖子正文（HTML）
      topic["title"]          → 话题标题（部分类型有）
    """
    talk = topic.get("talk", {}) or {}

    # 优先取文件名
    for f in talk.get("files", []) or []:
        name = (f.get("name") or "").strip()
        if name and len(name) >= 3:
            return {"书名": name, "分类": category}

    # 其次取帖子第一行文字
    name = first_line(talk.get("text", ""))
    if not name:
        name = first_line(topic.get("title", ""))
    if name and len(name) >= 3:
        return {"书名": name, "分类": category}

    return None


async def scroll_load(page, max_tries: int = 40):
    """滚动到底部，触发分页加载"""
    prev = -1
    for _ in range(max_tries):
        h = await page.evaluate("document.body.scrollHeight")
        if h == prev:
            break
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.2)
        prev = h


# ── 主流程 ────────────────────────────────────────────────────────────────────

async def main():
    raw_responses: list[dict] = []   # 存储拦截到的 API 原始数据

    async def on_response(response):
        if ZSXQ_API not in response.url:
            return
        try:
            body = await response.json()
            if body.get("succeeded"):
                raw_responses.append({"url": response.url, "data": body.get("resp_data", {})})
        except Exception:
            pass

    args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    if PROXY:
        args.append(f"--proxy-server={PROXY}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, args=args)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        page.on("response", on_response)

        await page.goto("https://wx.zsxq.com", wait_until="domcontentloaded", timeout=40000)

        print()
        print("=" * 58)
        print(" 知识星球书单提取 — 操作说明 ")
        print("=" * 58)
        print(" 1. 在浏览器里登录知识星球（微信扫码）")
        print(" 2. 进入「追梦人的财经图书馆」星球")
        print(" 3. 逐一点击每个分类文件夹（文件夹图标）")
        print("    每点进一个 → 滚动到底部 → 再点下一个")
        print(" 4. 所有分类都点完后，回到这里按 Enter")
        print("=" * 58)
        input(">>> 全部分类处理完后按 Enter：")

        # 捎带截图，方便调试
        await page.screenshot(path=str(Path(__file__).parent / "zsxq_debug.png"))
        await browser.close()

    # ── 解析拦截数据 ──────────────────────────────────────────────────────────
    print(f"\n共拦截到 {len(raw_responses)} 个 API 响应，开始解析…")

    # 保存原始数据供调试
    DEBUG_JSON.write_text(
        json.dumps(raw_responses, ensure_ascii=False, indent=2)[:500_000],
        encoding="utf-8"
    )

    # 建立 column_id → 分类名 映射
    col_map: dict[str, str] = {}
    for r in raw_responses:
        d = r["data"]
        # 分类列表接口
        for col in d.get("columns", []) or []:
            cid  = str(col.get("column_id", ""))
            name = col.get("title", "").strip()
            if cid and name:
                col_map[cid] = name
        # 部分接口在 group 里放 columns
        for col in (d.get("group", {}) or {}).get("columns", []) or []:
            cid  = str(col.get("column_id", ""))
            name = col.get("title", "").strip()
            if cid and name:
                col_map[cid] = name

    print(f"识别到 {len(col_map)} 个分类：{list(col_map.values())}")

    # 提取话题/文件
    books: list[dict] = []
    seen:  set[tuple]  = set()

    for r in raw_responses:
        d = r["data"]
        topics = d.get("topics", []) or []
        if not topics:
            continue

        # 从 URL 推断分类
        col_id_m = re.search(r"column_id=(\d+)", r["url"])
        col_id   = col_id_m.group(1) if col_id_m else ""
        category = col_map.get(col_id, "未分类")

        for topic in topics:
            entry = parse_topic(topic, category)
            if entry:
                key = (entry["书名"], entry["分类"])
                if key not in seen:
                    seen.add(key)
                    books.append(entry)

    if not books:
        print("\n⚠  未能提取到书目。可能原因：")
        print("   · 每个分类要点进去后再滚到底（不能只停在星球首页）")
        print("   · 原始响应已保存到 zsxq_raw.json，发给我来调试")
        return

    # 按分类排序
    books.sort(key=lambda b: (b["分类"], b["书名"]))

    # 写出 CSV
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

    print(f"\n✅ 书单已保存：{OUTPUT_CSV}")
    print(f"   共 {len(books)} 条\n")

    counts = Counter(b["分类"] for b in books)
    for cat, cnt in sorted(counts.items()):
        print(f"   {cat}：{cnt} 本")

    print(f"\n下一步：把 {OUTPUT_CSV.name} 改名为 books_input.csv，")
    print("再运行 search_anna.py 搜索下载链接。")


if __name__ == "__main__":
    asyncio.run(main())
