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

def get_download_url(md5: str, session: requests.Session) -> str | None:
    """调用 fast_download JSON 接口，拿到直接下载链接"""
    for index in range(3):   # 最多试 3 个镜像
        try:
            resp = session.get(
                f"{BASE_URL}/fast_download/{md5}/{index}",
                params={"key": API_KEY},
                timeout=20,
                allow_redirects=False,
            )
            # 302 跳转 → 直链
            if resp.status_code in (301, 302, 303):
                return resp.headers.get("Location")
            # JSON 响应
            if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
                data = resp.json()
                url = data.get("download_url") or data.get("url")
                if url:
                    return url
        except Exception:
            continue
    return None


def download_file(url: str, title: str, session: requests.Session) -> Path | None:
    """把文件下载到本地"""
    try:
        resp = session.get(url, timeout=120, stream=True)
        if resp.status_code != 200:
            return None

        # 从 Content-Disposition 或 URL 猜扩展名
        cd = resp.headers.get("content-disposition", "")
        m = re.search(r'filename="?([^";\n]+)"?', cd)
        fname = m.group(1).strip() if m else ""
        ext = fname.rsplit(".", 1)[-1] if "." in fname else \
              url.rsplit(".", 1)[-1].split("?")[0][:5] or "pdf"

        save_path = DOWNLOAD_DIR / f"{safe_name(title)}.{ext}"
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)

        size_mb = save_path.stat().st_size / 1024 / 1024
        if size_mb < 0.05:
            save_path.unlink(missing_ok=True)
            return None
        print(f"    ✅ {save_path.name}  ({size_mb:.1f} MB)")
        return save_path
    except Exception as e:
        print(f"    ⚠ 下载出错：{e}")
        return None


# ── 主流程 ────────────────────────────────────────────

def main():
    if API_KEY == "你的密钥粘贴在这里":
        print("❌ 请先在脚本第 16 行填入你的 API Key，再运行！")
        return

    DOWNLOAD_DIR.mkdir(exist_ok=True)
    books   = load_books()
    done    = load_done()
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
            dl_url = get_download_url(md5, session)
        else:
            # 没有 MD5，直接用 Excel 里的外部链接下载
            dl_url = book["url"] if book["url"].startswith("http") else None

        if not dl_url:
            print(f"    ❌ 拿不到下载链接，跳过")
            fail += 1
        else:
            result = download_file(dl_url, title, session)
            if result:
                success += 1
                mark_done(num)
            else:
                print(f"    ❌ 下载失败")
                fail += 1

        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    print(f"\n🎉 完成！成功 {success} | 失败 {fail}")


if __name__ == "__main__":
    main()
