#!/usr/bin/env python3
"""
PDF 重命名脚本
读取「下载书籍」文件夹里的每个 PDF，按以下优先级提取书名：
  1. PDF 元数据 metadata["title"]
  2. 封面第一页文字的第一行
重命名文件，操作记录写入 rename_log.csv（可用于手动回滚）

运行：python rename_pdfs.py
依赖：pip install pymupdf
"""

import csv
import re
import sys
from pathlib import Path
from tkinter import Tk, filedialog, messagebox

try:
    import fitz  # PyMuPDF
except ImportError:
    print("❌ 缺少依赖，请先运行：pip install pymupdf")
    sys.exit(1)

_HERE    = Path(__file__).parent
LOG_FILE = _HERE / "rename_log.csv"
BOOK_EXTS = {".pdf", ".epub", ".mobi", ".djvu", ".azw3", ".fb2"}


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def pick_folder(title: str) -> Path | None:
    root = Tk(); root.withdraw(); root.attributes("-topmost", True)
    path = filedialog.askdirectory(title=title)
    root.destroy()
    return Path(path) if path else None


def clean_filename(text: str) -> str:
    """去掉文件名非法字符，截断到 80 字"""
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:80].strip()


def is_garbage(text: str) -> bool:
    """判断字符串是否乱码/无意义（中英文有效字符比例 < 30%）"""
    if not text or len(text.strip()) < 2:
        return True
    valid = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or c.isalpha() or c.isdigit())
    return valid / max(len(text), 1) < 0.3


def extract_title(pdf_path: Path) -> tuple[str, str]:
    """
    返回 (书名, 来源)
    来源为 "元数据" 或 "封面文字"，失败返回 ("", "")
    """
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        return "", ""

    # ── 方法一：PDF 元数据 title ──
    meta  = doc.metadata or {}
    title = (meta.get("title") or "").strip()
    if title and not is_garbage(title):
        doc.close()
        return title, "元数据"

    # ── 方法二：封面第一页文字首行 ──
    if doc.page_count > 0:
        try:
            text  = doc[0].get_text("text")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            for line in lines[:20]:          # 只看前 20 行
                if len(line) >= 4 and not is_garbage(line):
                    doc.close()
                    return line, "封面文字"
        except Exception:
            pass

    doc.close()
    return "", ""


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    root = Tk(); root.withdraw()
    messagebox.showinfo(
        "PDF 重命名脚本",
        "接下来选择「下载书籍」文件夹。\n\n"
        "脚本会读取 PDF 的元数据或封面文字提取书名，自动重命名。\n"
        "所有操作记录写入 rename_log.csv，如有误可手动回滚。"
    )
    root.destroy()

    dl_folder = pick_folder("选择「下载书籍」文件夹")
    if not dl_folder:
        print("已取消"); return

    files = sorted(f for f in dl_folder.iterdir() if f.suffix.lower() in BOOK_EXTS)
    print(f"📂 找到 {len(files)} 个文件\n")

    renamed = skipped = failed = 0
    log_rows = []

    for f in files:
        old_name = f.name
        ext      = f.suffix.lower()

        title, source = extract_title(f)

        if not title:
            print(f"  [跳过] {old_name[:55]}（无法提取书名）")
            log_rows.append({"旧文件名": old_name, "新文件名": old_name,
                             "来源": "—", "状态": "跳过-无法提取"})
            skipped += 1
            continue

        new_stem = clean_filename(title)
        if not new_stem:
            skipped += 1
            continue

        new_path = f.parent / f"{new_stem}{ext}"

        # 名字未变
        if new_path == f:
            log_rows.append({"旧文件名": old_name, "新文件名": old_name,
                             "来源": source, "状态": "无需改动"})
            skipped += 1
            continue

        # 冲突处理
        counter = 1
        while new_path.exists():
            new_path = f.parent / f"{new_stem}_{counter}{ext}"
            counter += 1

        try:
            f.rename(new_path)
            print(f"  ✅ [{source}] {old_name[:40]}")
            print(f"       → {new_path.name[:55]}")
            log_rows.append({"旧文件名": old_name, "新文件名": new_path.name,
                             "来源": source, "状态": "已重命名"})
            renamed += 1
        except Exception as e:
            print(f"  ❌ 重命名失败：{e}")
            log_rows.append({"旧文件名": old_name, "新文件名": "",
                             "来源": source, "状态": f"失败：{e}"})
            failed += 1

    # 保存日志
    with open(LOG_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["旧文件名", "新文件名", "来源", "状态"])
        writer.writeheader()
        writer.writerows(log_rows)

    summary = (f"✅ 已重命名：{renamed}\n"
               f"⏭ 跳过（无需改动/无法提取）：{skipped}\n"
               f"❌ 失败：{failed}\n\n"
               f"操作记录：{LOG_FILE}")
    print(f"\n{summary}")

    root = Tk(); root.withdraw()
    messagebox.showinfo("重命名完成", summary)
    root.destroy()


if __name__ == "__main__":
    main()
