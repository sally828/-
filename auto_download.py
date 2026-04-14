#!/usr/bin/env python3
"""
Anna's Archive 自动下载脚本
运行前只需改第 14 行的 API_KEY，其他不用动。
运行：python auto_download.py
"""

import re
import time
import random
from pathlib import Path

import openpyxl
import requests

# ══════════════════════════════════════════════════════
API_KEY      = "你的密钥粘贴在这里"   # ← 只改这一行
PROXY        = "http://127.0.0.1:10808"
# ══════════════════════════════════════════════════════

_HERE        = Path(__file__).parent
EXCEL_FILE   = _HERE / "书目搜索结果.xlsx"
DOWNLOAD_DIR = _HERE / "下载书籍"
DONE_FILE    = _HERE / "downloaded.txt"
DELAY_MIN    = 2.0
DELAY_MAX    = 4.0
BASE_URL     = "https://annas-archive.gl"


def extract_md5(url: str) -> str | None:
    m = re.search(r'[a-f0-9]{32}', str(url or ""), re.I)
    return m.group(0).lower() if m else None

def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:80].strip()

def load_done() -> set:
    return set(DONE_FILE.read_text(encoding="utf-8").splitlines()) if DONE_FILE.exists() else set()

def mark_done(num: str):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(num + "\n")

def load_books() -> list[dict]:
    wb = openpyxl.load_workbook(EXCEL_FILE)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=False):
        data = {}
        for i, cell in enumerate(row):
            if i < len(headers) and headers[i]:
                data[headers[i]] = cell.hyperlink.target if cell.hyperlink else cell.value
        num  = str(data.get("序号", "") or "").strip()
        name = str(data.get("找到书名", "") or data.get("原书名", "") or "").strip()
        url  = str(data.get("下载链接", "") or "").strip()
        if num:
            rows.append({"序号": num, "书名": name or f"书_{num}", "url": url})
    return rows

def download_one(excel_url: str, title: str, session: requests.Session) -> Path | None:
    # 直接用 Excel 里的 URL，加上 API key
    dl_url = excel_url + ("&" if "?" in excel_url else "?") + f"key={API_KEY}"
    try:
        # 第一步：访问 fast_download 页面，跟随跳转拿到 CDN 真实地址
        resp = session.get(dl_url, timeout=30, allow_redirects=True)
        final_url = resp.url   # 跳转后的 CDN 地址

        ct = resp.headers.get("content-type", "")
        if "html" in ct:
            # 返回了页面而非文件，说明 key 无效或需要登录
            print(f"    ❌ 返回 HTML，key 可能无效")
            return None

        # 第二步：保存文件内容
        cd = resp.headers.get("content-disposition", "")
        m = re.search(r'filename[^;=\n]*=[\'""]?([^\'";\n]+)', cd)
        fname = m.group(1).strip() if m else ""
        ext = (fname.rsplit(".", 1)[-1] if "." in fname
               else final_url.split("?")[0].rsplit(".", 1)[-1][:5] or "pdf")

        save_path = DOWNLOAD_DIR / f"{safe_name(title)}.{ext}"
        with open(save_path, "wb") as f:
            f.write(resp.content)

        size_mb = save_path.stat().st_size / 1024 / 1024
        if size_mb < 0.05:
            save_path.unlink(missing_ok=True)
            print(f"    ❌ 文件太小，可能下载失败")
            return None
        print(f"    ✅ {save_path.name}  ({size_mb:.1f} MB)")
        return save_path
    except Exception as e:
        print(f"    ❌ 出错：{e}")
        return None

def main():
    if API_KEY == "你的密钥粘贴在这里":
        print("❌ 请先填入 API Key（第 14 行），再运行！")
        return

    DOWNLOAD_DIR.mkdir(exist_ok=True)
    books   = load_books()
    done    = load_done()
    pending = [b for b in books if b["序号"] not in done and extract_md5(b["url"])]

    print(f"📚 共 {len(books)} 条 | 已完成 {len(done)} | 本次下载 {len(pending)} 条")
    print(f"📁 保存到: {DOWNLOAD_DIR}\n")
    if not pending:
        print("✅ 全部完成！")
        return

    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    )
    session.proxies = {"http": PROXY, "https": PROXY}

    success = fail = 0
    for i, book in enumerate(pending):
        num, title = book["序号"], book["书名"]
        print(f"[{i+1:4d}/{len(pending)}] #{num}  {title[:50]}")
        if download_one(book["url"], title, session):
            success += 1
            mark_done(num)
        else:
            fail += 1
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    print(f"\n🎉 完成！成功 {success} | 失败 {fail}")

if __name__ == "__main__":
    main()
