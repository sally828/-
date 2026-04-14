#!/usr/bin/env python3
"""
Anna's Archive 自动下载脚本 v4（官方 JSON API 版）
不需要打开浏览器，直接通过 API 获取下载链接并保存文件

运行前：把下面的"你的密钥粘贴在这里"替换成真实密钥（见注释）
运行：python auto_download.py
"""

import re
import time
import random
from pathlib import Path

import requests
import openpyxl

# ══════════════════════════════════════════════════════════════════════════════
#  只需要改这一行 ↓↓↓
# ══════════════════════════════════════════════════════════════════════════════
API_KEY = "你的密钥粘贴在这里"
# 获取方式：用浏览器打开 annas-archive.gl → 登录账号 → 进入"账户"页面
#           找到"密钥（请勿分享！）："那一行，点"显示"，复制那串字符粘贴到上面
# ══════════════════════════════════════════════════════════════════════════════

EXCEL_FILE   = "书目搜索结果.xlsx"
DOWNLOAD_DIR = Path("下载书籍")
DONE_FILE    = "downloaded.txt"
PROXY        = "http://127.0.0.1:10808"   # V2RayN 代理，不用改
BASE_URL     = "https://annas-archive.gl"

PROXIES = {"http": PROXY, "https": PROXY}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    )
}

# 这些链接是无效的，跳过
INVALID_PATTERNS = ["/account/", "javascript:", "annas-archive.gl/#",
                    "/donate", "/faq", "/blog", "/search", "/datasets"]


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def extract_md5(url: str) -> str | None:
    """从 URL 中提取 32 位 MD5 哈希值"""
    m = re.search(r'/md5/([a-f0-9]{32})', url, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r'[?&]md5=([a-f0-9]{32})', url, re.I)
    if m:
        return m.group(1).lower()
    return None


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:80].strip()


def is_invalid_url(url: str) -> bool:
    if not url or not url.startswith("http"):
        return True
    return any(p in url for p in INVALID_PATTERNS)


# ── API 调用 ──────────────────────────────────────────────────────────────────

def get_fast_download_url(md5: str) -> str | None:
    """调用官方 JSON API，返回直链；失败返回 None"""
    try:
        resp = requests.get(
            f"{BASE_URL}/dyn/api/fast_download.json",
            params={"md5": md5, "key": API_KEY},
            headers=HEADERS,
            proxies=PROXIES,
            timeout=20,
        )
        if resp.status_code == 200:
            data = resp.json()
            # 兼容多种可能的响应格式
            for key in ("download_urls", "urls"):
                if isinstance(data.get(key), list) and data[key]:
                    return data[key][0]
            for key in ("url", "download_url", "link"):
                if data.get(key):
                    return data[key]
            print(f"      ⚠ API 返回未知格式: {list(data.keys())}")
        elif resp.status_code == 401:
            print(f"      ❌ API 密钥无效，请检查 API_KEY 是否填写正确")
        elif resp.status_code == 429:
            print(f"      ⏳ 下载次数已用完（每 18 小时 1000 次），请稍后再试")
        else:
            print(f"      ⚠ API 返回 {resp.status_code}: {resp.text[:120]}")
    except Exception as e:
        print(f"      ⚠ API 请求失败: {e}")
    return None


# ── 文件下载 ──────────────────────────────────────────────────────────────────

def download_file(url: str, save_base: Path) -> bool:
    """流式下载文件，自动推断扩展名；成功返回 True"""
    # Anna's Archive fast_download 链接需要带上 API key
    params = {}
    if "annas-archive" in url and API_KEY and API_KEY != "你的密钥粘贴在这里":
        params["key"] = API_KEY

    try:
        resp = requests.get(
            url, headers=HEADERS, proxies=PROXIES,
            params=params,
            stream=True, timeout=120, allow_redirects=True,
        )
        resp.raise_for_status()

        # ── 验证是真正的文件而非网页 ─────────────────────────────────
        ct = resp.headers.get("Content-Type", "").lower()
        if "text/html" in ct or "text/plain" in ct:
            print(f"      ❌ 服务器返回的是网页而非书籍（Content-Type: {ct}）")
            return False

        # 读取前 512 字节检查文件头（magic bytes）
        first_chunk = b""
        chunks = []
        for chunk in resp.iter_content(65536):
            if chunk:
                if not first_chunk:
                    first_chunk = chunk
                    head = first_chunk[:16].lower()
                    # HTML 页面标志
                    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
                        print(f"      ❌ 下载内容是 HTML 网页，不是书籍文件（密钥可能需要随 URL 传递）")
                        return False
                    # 检查已知书籍格式：PDF / EPUB(zip) / MOBI / DJVU
                    is_book = (
                        first_chunk[:4] == b"%PDF" or          # PDF
                        first_chunk[:2] == b"PK"   or          # EPUB / ZIP
                        b"BOOKMOBI" in first_chunk[:16] or     # MOBI
                        first_chunk[:4] == b"AT&T" or          # DJVU
                        first_chunk[:4] == b"\xd0\xcf\x11\xe0" # DOC
                    )
                    if not is_book:
                        # 不是已知格式，检查内容是否含 HTML 标签
                        sample = first_chunk[:512]
                        if b"<html" in sample or b"<body" in sample or b"cloudflare" in sample.lower():
                            print(f"      ❌ 内容疑似网页（文件头: {first_chunk[:8]!r}）")
                            return False
                chunks.append(chunk)

        if not chunks:
            print(f"      ❌ 响应为空")
            return False

        # ── 推断扩展名 ───────────────────────────────────────────────
        ext = "pdf"
        if "epub" in ct:
            ext = "epub"
        elif "mobi" in ct or "mobipocket" in ct:
            ext = "mobi"
        elif "djvu" in ct:
            ext = "djvu"

        cd = resp.headers.get("Content-Disposition", "")
        m = re.search(r'filename[^;=\n]*=["\']?([^"\'\n;]+)', cd)
        if m:
            orig = m.group(1).strip().strip('"\'')
            if "." in orig:
                candidate = orig.rsplit(".", 1)[-1].lower()
                if candidate in ("pdf", "epub", "mobi", "djvu", "azw3",
                                 "fb2", "doc", "docx"):
                    ext = candidate

        # 根据文件头二次确认格式
        if first_chunk[:4] == b"%PDF":
            ext = "pdf"
        elif first_chunk[:2] == b"PK":
            ext = "epub"

        save_path = save_base.parent / f"{save_base.name}.{ext}"
        if save_path.exists():
            save_path.unlink()

        with open(save_path, "wb") as f:
            for c in chunks:
                f.write(c)

        size_mb = save_path.stat().st_size / 1024 / 1024
        if size_mb < 0.05:
            print(f"      ❌ 文件过小（{size_mb:.2f} MB），可能是错误页面")
            save_path.unlink()
            return False

        print(f"      ✅ {save_path.name}  ({size_mb:.1f} MB)")
        return True

    except Exception as e:
        print(f"      ❌ 下载失败: {e}")
        return False


# ── Excel 读取 ────────────────────────────────────────────────────────────────

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
        name = str(data.get("找到书名", "") or data.get("原书名", "") or f"书_{num}").strip()
        url  = str(data.get("下载链接", "") or "").strip()
        if num:
            rows.append({"序号": num, "书名": name, "url": url})
    return rows


def load_done() -> set:
    if Path(DONE_FILE).exists():
        return set(Path(DONE_FILE).read_text(encoding="utf-8").splitlines())
    return set()


def mark_done(num: str):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(num + "\n")


def clean_corrupted_files() -> int:
    """扫描下载文件夹，删除文件头是 HTML 的损坏文件，返回删除数量"""
    if not DOWNLOAD_DIR.exists():
        return 0
    deleted = 0
    book_exts = {".pdf", ".epub", ".mobi", ".djvu", ".azw3", ".fb2"}
    for f in DOWNLOAD_DIR.iterdir():
        if f.suffix.lower() not in book_exts:
            continue
        try:
            with open(f, "rb") as fh:
                head = fh.read(16)
            is_html = (
                head.startswith(b"<!") or
                head.lower().startswith(b"<html") or
                head.startswith(b"\r\n<!") or
                head.startswith(b"\n<!") or
                b"<html" in head
            )
            # PDF / EPUB(PK) / MOBI / DJVU
            is_valid = (
                head[:4] == b"%PDF" or
                head[:2] == b"PK" or
                b"BOOKMOBI" in head or
                head[:4] == b"AT&T" or
                head[:4] == b"\xd0\xcf\x11\xe0"
            )
            if is_html or (not is_valid and f.stat().st_size < 200 * 1024):
                f.unlink()
                deleted += 1
        except Exception:
            pass
    return deleted


# ── 单本处理 ──────────────────────────────────────────────────────────────────

def process_book(book: dict) -> bool:
    url   = book["url"]
    title = book["书名"]
    save_base = DOWNLOAD_DIR / safe_name(title)

    # 无效链接
    if is_invalid_url(url):
        print(f"    ⚠ 链接无效，跳过（需要重新运行 search_anna.py）")
        return False

    # 有 MD5 → 调用 API
    md5 = extract_md5(url)
    if md5:
        print(f"    → API 快速下载 (md5={md5[:8]}…)")
        dl_url = get_fast_download_url(md5)
        if dl_url:
            print(f"    → {dl_url[:75]}")
            return download_file(dl_url, save_base)
        return False

    # 无 MD5（直接镜像链接）→ 尝试直接下载
    print(f"    → 直接下载: {url[:65]}")
    return download_file(url, save_base)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    # 检查密钥
    if API_KEY == "你的密钥粘贴在这里" or not API_KEY.strip():
        print("=" * 60)
        print("❌ 还没有填写 API 密钥，脚本无法运行")
        print()
        print("操作步骤：")
        print("  1. 用记事本打开 auto_download.py")
        print('  2. 找到这一行：API_KEY = "你的密钥粘贴在这里"')
        print("  3. 把引号里的文字替换成从账户页面复制的密钥")
        print("  4. 保存文件（Ctrl+S），重新运行")
        print("=" * 60)
        return

    DOWNLOAD_DIR.mkdir(exist_ok=True)

    # 清理上次下载的损坏文件（HTML 网页被误存为 PDF/EPUB）
    cleaned = clean_corrupted_files()
    if cleaned:
        print(f"🗑  已自动删除 {cleaned} 个损坏文件（HTML 网页），将重新下载")
        # 同时清除 downloaded.txt 里对应的记录，让这些书重新排队
        # （因为我们不知道哪些序号对应损坏文件，全部重置更安全）
        if Path(DONE_FILE).exists():
            Path(DONE_FILE).unlink()
        print("   downloaded.txt 已重置，全部重新下载")

    books = load_books()
    done  = load_done()

    all_pending  = [b for b in books if b["序号"] not in done]
    invalid_books = [b for b in all_pending if is_invalid_url(b["url"])]
    valid_books   = [b for b in all_pending if not is_invalid_url(b["url"])]

    print(f"📚 共 {len(books)} 条 | 已完成 {len(done)} | 待处理 {len(all_pending)}")
    if invalid_books:
        print(f"⚠  {len(invalid_books)} 条链接无效（旧版脚本生成，运行 search_anna.py 可修复）")
    print(f"🔗 可下载: {len(valid_books)} 条")
    print(f"📁 保存到: {DOWNLOAD_DIR.absolute()}")
    print()

    if not valid_books:
        if invalid_books:
            print("💡 提示：请重新运行 search_anna.py 更新下载链接，然后再运行本脚本")
        else:
            print("✅ 全部完成！")
        return

    success = fail = 0
    for i, book in enumerate(valid_books):
        num, title = book["序号"], book["书名"]
        print(f"[{i+1:4d}/{len(valid_books)}] #{num}  {title[:50]}")

        ok = process_book(book)
        if ok:
            success += 1
            mark_done(num)
        else:
            fail += 1

        # 下载间隔（API 有次数限制，稍微等一下）
        time.sleep(random.uniform(0.5, 1.5))

    print()
    print(f"🎉 完成！成功 {success} | 失败 {fail}")
    if invalid_books:
        print(f"💡 还有 {len(invalid_books)} 条需要重新搜索，运行 search_anna.py 后可继续")


if __name__ == "__main__":
    main()
