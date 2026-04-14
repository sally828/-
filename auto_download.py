#!/usr/bin/env python3
"""
Anna's Archive 自动下载脚本（API 版）
- 用会员 API key 直接拿下载链接，不开浏览器
- 运行前把下面 API_KEY 那行填好，保存，再运行

运行：python auto_download.py
"""

import csv
import re
import time
import random
from pathlib import Path

import openpyxl
import requests

# ══════════════════════════════════════════════════════
#  填入你的 API Key（账号页面 → 显示 → 复制那串字符）
# ══════════════════════════════════════════════════════
API_KEY = "你的密钥粘贴在这里"
# ══════════════════════════════════════════════════════

_HERE        = Path(__file__).parent
EXCEL_FILE   = _HERE / "书目搜索结果.xlsx"
DOWNLOAD_DIR = _HERE / "下载书籍"
DONE_FILE    = _HERE / "downloaded.txt"

BASE_URL     = "https://annas-archive.gl"
DELAY_MIN    = 2.0
DELAY_MAX    = 4.0


# ── 工具函数 ──────────────────────────────────────────

def extract_md5(url: str) -> str | None:
    # 匹配 /md5/、/slow_download/、/fast_download/ 后面的 MD5
    m = re.search(r'/(?:md5|slow_download|fast_download)/([a-f0-9]{32})', str(url or ""), re.I)
    if m:
        return m.group(1).lower()
    # 兜底：URL 里任意位置的 32 位十六进制串
    m = re.search(r'\b([a-f0-9]{32})\b', str(url or ""), re.I)
    return m.group(1).lower() if m else None

def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:80].strip()

def load_done() -> set:
    if DONE_FILE.exists():
        return set(DONE_FILE.read_text(encoding="utf-8").splitlines())
    return set()

def mark_done(num: str):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(num + "\n")


# ── 读取 Excel ────────────────────────────────────────

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
        num        = str(data.get("序号", "") or "").strip()
        found_name = str(data.get("找到书名", "") or "").strip()
        orig_name  = str(data.get("原书名", "") or "").strip()
        url        = str(data.get("下载链接", "") or "").strip()
        if num:
            rows.append({
                "序号":  num,
                "书名":  found_name or orig_name or f"书_{num}",
                "url":   url,
            })
    return rows


# ── API 下载 ──────────────────────────────────────────

def try_download(md5: str, title: str, session: requests.Session) -> Path | None:
    """
    依次尝试 fast_download 的 0、1、2 号镜像，
    直接跟随跳转下载文件，成功则保存并返回路径。
    """
    for index in range(3):
        url = f"{BASE_URL}/fast_download/{md5}/{index}?key={API_KEY}"
        try:
            resp = session.get(url, timeout=120, stream=True, allow_redirects=True)
            ct = resp.headers.get("content-type", "")

            # 是文件流（非 HTML）→ 直接保存
            if resp.status_code == 200 and "html" not in ct:
                cd = resp.headers.get("content-disposition", "")
                m = re.search(r'filename[^;=\n]*=[\'""]?([^\'"\n;]+)', cd)
                fname = m.group(1).strip() if m else ""
                ext = (fname.rsplit(".", 1)[-1]
                       if "." in fname
                       else url.split("?")[0].rsplit(".", 1)[-1][:5] or "pdf")

                save_path = DOWNLOAD_DIR / f"{safe_name(title)}.{ext}"
                with open(save_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        f.write(chunk)

                size_mb = save_path.stat().st_size / 1024 / 1024
                if size_mb < 0.05:
                    save_path.unlink(missing_ok=True)
                    continue
                print(f"    ✅ {save_path.name}  ({size_mb:.1f} MB)")
                return save_path

            # 收到 HTML → 可能是限速页面，换下一个镜像
            if resp.status_code in (429, 403, 401):
                print(f"    ⚠ 镜像{index} 返回 {resp.status_code}，换下一个…")
                continue

        except Exception as e:
            print(f"    ⚠ 镜像{index} 出错：{e}")
            continue

    return None


# ── 主流程 ────────────────────────────────────────────

def main():
    if API_KEY == "你的密钥粘贴在这里":
        print("❌ 请先在脚本第 16 行填入你的 API Key，再运行！")
        return

    DOWNLOAD_DIR.mkdir(exist_ok=True)
    books   = load_books()
    done    = load_done()

    # 调试：统计链接情况
    has_url   = [b for b in books if b["url"].startswith("http")]
    has_md5   = [b for b in books if extract_md5(b["url"])]
    no_url    = [b for b in books if not b["url"] or b["url"] == "点击下载"]
    print(f"🔍 链接统计：有效URL={len(has_url)} | 含MD5={len(has_md5)} | 无链接={len(no_url)}")
    if has_url:
        print(f"   URL样例：{has_url[0]['url'][:80]}")

    # 有 MD5 的走 API，没有 MD5 但有 URL 的直接下载外部链接
    pending = [b for b in books if b["序号"] not in done
               and (extract_md5(b["url"]) or b["url"].startswith("http"))]

    print(f"📚 共 {len(books)} 条 | 已完成 {len(done)} | 本次下载 {len(pending)} 条")
    print(f"📁 保存到: {DOWNLOAD_DIR}\n")

    if not pending:
        print("✅ 全部完成！")
        return

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/121.0.0.0 Safari/537.36",
    })

    success = fail = 0
    for i, book in enumerate(pending):
        num, title = book["序号"], book["书名"]
        print(f"[{i+1:4d}/{len(pending)}] #{num}  {title[:50]}")

        md5 = extract_md5(book["url"])
        if md5:
            result = try_download(md5, title, session)
        else:
            print(f"    ⚠ 无 MD5，跳过")
            result = None

        if result:
            success += 1
            mark_done(num)
        else:
            fail += 1

        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    print(f"\n🎉 完成！成功 {success} | 失败 {fail}")


if __name__ == "__main__":
    main()
