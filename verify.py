#!/usr/bin/env python3
"""
书目核验脚本
对比：原始书单 / 搜索结果 / 下载文件夹，输出核验报告 Excel

运行：python verify.py
运行后会弹出三个选择框，分别选择对应的文件/文件夹
"""

import csv
import re
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import Tk, filedialog, messagebox

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


# ── 弹窗选择路径 ──────────────────────────────────────────────────────────────

def pick_file(title, filetypes=(("CSV", "*.csv"), ("所有文件", "*.*"))):
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(title=title, filetypes=filetypes)
    root.destroy()
    return path or None


def pick_folder(title):
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askdirectory(title=title)
    root.destroy()
    return path or None


def pick_save(title, default="核验报告.xlsx"):
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.asksaveasfilename(
        title=title,
        defaultextension=".xlsx",
        filetypes=[("Excel", "*.xlsx")],
        initialfile=default,
    )
    root.destroy()
    return path or None


# ── 数据读取 ──────────────────────────────────────────────────────────────────

def load_books_input(path: str) -> list[dict]:
    """读取原始书单 books_input.csv"""
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            num = str(row.get("序号", "")).strip()
            if num:
                rows.append({
                    "序号":   num,
                    "分类":   str(row.get("分类", "")).strip(),
                    "原书名": str(row.get("原书名", "")).strip(),
                })
    return rows


def load_search_progress(path: str) -> dict[str, dict]:
    """读取搜索进度 search_progress.csv，以序号为 key"""
    result = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            num = str(row.get("序号", "")).strip()
            if num:
                result[num] = {
                    "找到书名": str(row.get("找到书名", "")).strip(),
                    "状态":     str(row.get("状态", "")).strip(),
                    "下载链接": str(row.get("下载链接", "")).strip(),
                }
    return result


def load_downloaded_files(folder: str) -> dict[str, tuple[str, float]]:
    """
    扫描下载文件夹，返回 {规范化书名: (原始文件名, 大小MB)}
    用于模糊匹配
    """
    files = {}
    book_exts = {".pdf", ".epub", ".mobi", ".djvu", ".azw3", ".fb2", ".doc", ".docx"}
    for f in Path(folder).iterdir():
        if f.suffix.lower() in book_exts:
            size_mb = f.stat().st_size / 1024 / 1024
            files[normalize(f.stem)] = (f.name, round(size_mb, 2))
    return files


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """去掉标点空格，统一小写，方便比较"""
    return re.sub(r'[\s\W_]+', '', text).lower()


def similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度 0~1"""
    if not a or not b:
        return 0.0
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def find_file(title: str, downloaded: dict) -> tuple[str, float, float]:
    """
    在下载文件夹中找最匹配的文件
    返回 (文件名, 相似度, 文件大小MB)；找不到返回 ("", 0, 0)
    """
    if not title:
        return "", 0.0, 0.0
    nt = normalize(title)
    best_score = 0.0
    best_name  = ""
    best_size  = 0.0
    for norm_stem, (fname, size) in downloaded.items():
        sc = SequenceMatcher(None, nt, norm_stem).ratio()
        if sc > best_score:
            best_score = sc
            best_name  = fname
            best_size  = size
    if best_score >= 0.5:
        return best_name, round(best_score * 100, 1), best_size
    return "", 0.0, 0.0


def classify_row(search: dict | None, filename: str, sim: float, file_sim: float) -> str:
    """生成备注"""
    if not search:
        return "⚠ 未搜索"
    状态 = search.get("状态", "")
    if "未找到" in 状态:
        return "❌ Anna's Archive 未收录"
    if not filename:
        return "⏳ 未下载"
    if file_sim < 60:
        return "⚠ 文件名差异较大，请人工确认"
    if sim < 60:
        return "⚠ 搜索结果与原书名差异较大"
    return "✅ 正常"


# ── 生成 Excel ────────────────────────────────────────────────────────────────

FILL_OK      = PatternFill("solid", fgColor="E2EFDA")   # 绿
FILL_WARN    = PatternFill("solid", fgColor="FFEB9C")   # 黄
FILL_ERR     = PatternFill("solid", fgColor="FCE4D6")   # 红
FILL_MISSING = PatternFill("solid", fgColor="F2F2F2")   # 灰
FILL_HEADER  = PatternFill("solid", fgColor="1F4E79")
FONT_HEADER  = Font(bold=True, color="FFFFFF", size=11)
FONT_LINK    = Font(color="0563C1", underline="single")


def row_fill(note: str) -> PatternFill:
    if "✅" in note:
        return FILL_OK
    if "⏳" in note or "未搜索" in note:
        return FILL_MISSING
    if "❌" in note:
        return FILL_ERR
    return FILL_WARN


def save_excel(rows: list[dict], out_path: str):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "核验报告"

    headers = ["序号", "分类", "原书名", "找到书名", "书名相似度%",
               "下载文件名", "文件大小MB", "文件匹配度%", "备注"]
    widths  = [7,      15,     45,       45,        12,
               50,              12,         12,          28]

    for c, (h, w) in enumerate(zip(headers, widths), 1):
        cell = ws.cell(1, c, h)
        cell.fill, cell.font = FILL_HEADER, FONT_HEADER
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.row_dimensions[1].height = 28

    for r, row in enumerate(rows, 2):
        note = row["备注"]
        fill = row_fill(note)
        vals = [
            row["序号"], row["分类"], row["原书名"], row["找到书名"],
            row["书名相似度"] or "",
            row["文件名"], row["文件大小"] or "", row["文件匹配度"] or "",
            note,
        ]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(r, c, v)
            cell.fill = fill
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:I{len(rows)+1}"
    wb.save(out_path)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    root = Tk()
    root.withdraw()
    messagebox.showinfo(
        "书目核验脚本",
        "接下来弹出三个选择框：\n\n"
        "① 选择原始书单 books_input.csv\n"
        "② 选择搜索进度 search_progress.csv\n"
        "③ 选择下载书籍所在文件夹",
    )
    root.destroy()

    books_path = pick_file("① 选择原始书单 books_input.csv")
    if not books_path:
        print("已取消"); return

    progress_path = pick_file("② 选择搜索进度 search_progress.csv")
    if not progress_path:
        print("已取消"); return

    dl_folder = pick_folder("③ 选择下载书籍所在文件夹")
    if not dl_folder:
        print("已取消"); return

    out_path = pick_save("④ 选择核验报告保存位置")
    if not out_path:
        print("已取消"); return

    print("读取数据中…")
    books    = load_books_input(books_path)
    progress = load_search_progress(progress_path)
    dl_files = load_downloaded_files(dl_folder)

    print(f"  原始书单：{len(books)} 条")
    print(f"  搜索记录：{len(progress)} 条")
    print(f"  下载文件：{len(dl_files)} 个")
    print("对比中…")

    result_rows = []
    stats = {"ok": 0, "warn": 0, "no_download": 0, "not_found": 0, "no_search": 0}

    for book in books:
        num    = book["序号"]
        orig   = book["原书名"]
        search = progress.get(num)

        found_title = search["找到书名"] if search else ""
        title_sim   = round(similarity(orig, found_title) * 100, 1) if found_title else 0.0

        # 用找到的书名去匹配文件
        fname, file_sim, fsize = find_file(found_title or orig, dl_files)

        note = classify_row(search, fname, title_sim, file_sim)

        # 统计
        if "✅" in note:       stats["ok"] += 1
        elif "⏳" in note:     stats["no_download"] += 1
        elif "❌" in note:     stats["not_found"] += 1
        elif "未搜索" in note: stats["no_search"] += 1
        else:                   stats["warn"] += 1

        result_rows.append({
            "序号":     num,
            "分类":     book["分类"],
            "原书名":   orig,
            "找到书名": found_title,
            "书名相似度": title_sim if found_title else None,
            "文件名":   fname,
            "文件大小": fsize if fname else None,
            "文件匹配度": file_sim if fname else None,
            "备注":     note,
        })

    save_excel(result_rows, out_path)

    total = len(books)
    print(f"\n核验完成！共 {total} 本书")
    print(f"  ✅ 正常下载：{stats['ok']}")
    print(f"  ⏳ 未下载：  {stats['no_download']}")
    print(f"  ❌ 未收录：  {stats['not_found']}")
    print(f"  ⚠ 需人工确认：{stats['warn']}")
    print(f"  灰色 未搜索：{stats['no_search']}")
    print(f"\n报告已保存：{out_path}")

    root = Tk()
    root.withdraw()
    messagebox.showinfo("完成", f"核验报告已保存！\n\n"
                                f"✅ 正常下载：{stats['ok']}\n"
                                f"⏳ 未下载：{stats['no_download']}\n"
                                f"❌ 未收录：{stats['not_found']}\n"
                                f"⚠ 需人工确认：{stats['warn']}\n\n"
                                f"报告位置：{out_path}")
    root.destroy()


if __name__ == "__main__":
    main()
