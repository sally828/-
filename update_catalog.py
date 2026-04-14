#!/usr/bin/env python3
"""
将搜索结果写回书目 CSV

功能：读取 search_progress.csv 里的搜索结果，把「状态」和「找到书名」
更新回 books_input.csv，方便下次直接复用，无需保留进度文件。

运行方式：
    python update_catalog.py

可选参数：
    python update_catalog.py --input  books_input.csv
                             --progress search_progress.csv
                             --output  books_input.csv   # 默认覆盖原文件
"""

import argparse
import csv
import shutil
from pathlib import Path

INPUT_CSV    = "books_input.csv"
PROGRESS_CSV = "search_progress.csv"
FIELDS       = ["序号", "分类", "原书名", "状态", "找到书名"]


def load_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_progress(path: str) -> dict[str, dict]:
    """返回 {序号: {状态, 找到书名}} 的映射"""
    result: dict[str, dict] = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            num = str(row.get("序号", "")).strip()
            if num:
                result[num] = {
                    "状态":     str(row.get("状态", "")).strip(),
                    "找到书名": str(row.get("找到书名", "")).strip(),
                }
    return result


def write_csv(rows: list[dict], path: str):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="将搜索进度写回书目 CSV")
    parser.add_argument("--input",    default=INPUT_CSV,    help="原始书单 CSV")
    parser.add_argument("--progress", default=PROGRESS_CSV, help="搜索进度 CSV")
    parser.add_argument("--output",   default=None,         help="输出路径（默认覆盖 --input）")
    args = parser.parse_args()

    input_path    = args.input
    progress_path = args.progress
    output_path   = args.output or args.input

    if not Path(input_path).exists():
        print(f"❌ 找不到 {input_path}")
        return
    if not Path(progress_path).exists():
        print(f"❌ 找不到 {progress_path}，请先运行 search_anna.py")
        return

    books    = load_csv(input_path)
    progress = load_progress(progress_path)

    updated = skipped = already = 0
    for book in books:
        num = str(book.get("序号", "")).strip()
        old_status = book.get("状态", "").strip()

        # 已标记"OK 找到"的保持不变
        if old_status == "OK 找到":
            already += 1
            continue

        p = progress.get(num)
        if p is None:
            skipped += 1
            continue

        new_status = p["状态"]
        # 只接受明确的已找到/未找到结果，跳过空值
        if not new_status:
            skipped += 1
            continue

        book["状态"]     = new_status
        book["找到书名"] = p["找到书名"]
        updated += 1

    # 覆盖前备份（仅当输出路径与输入路径相同时）
    if Path(output_path).resolve() == Path(input_path).resolve():
        backup = input_path + ".bak"
        shutil.copy2(input_path, backup)
        print(f"📋 已备份原文件 → {backup}")

    write_csv(books, output_path)

    total = len(books)
    print(f"✅ 更新完成：{output_path}")
    print(f"   总计 {total} 条 | 本次更新 {updated} 条 | "
          f"原已标记 {already} 条 | 尚未搜索 {skipped} 条")


if __name__ == "__main__":
    main()
