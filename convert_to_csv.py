#!/usr/bin/env python3
"""
将书单原始文本转换为 books_input.csv

使用方法：
  1. 把你的书单（制表符分隔的那张表）另存为 books_raw.txt（UTF-8 编码）
  2. 运行：python convert_to_csv.py
  3. 生成 books_input.csv，供 search_anna.py 读取

原始文本格式（列之间用制表符分隔）：
  序号  分类  原书名  状态  找到书名
  4     商业...  经济学原理...  OK 找到  经济学原理...
  10    商业...  创造性破坏...  -- 未找到
  ...
"""

import csv
import re
import sys
from pathlib import Path

INPUT_TXT  = "books_raw.txt"
OUTPUT_CSV = "books_input.csv"


def parse_line(line: str) -> dict | None:
    """解析一行，支持制表符和多空格两种分隔方式"""
    line = line.rstrip("\n\r")
    if not line.strip():
        return None

    # 优先按制表符分隔
    if "\t" in line:
        parts = line.split("\t")
    else:
        # 多空格分隔（至少2个空格）
        parts = re.split(r"  +", line)

    parts = [p.strip() for p in parts]

    # 跳过标题行
    if parts[0] in ("序号", ""):
        return None

    # 必须有序号（纯数字）
    if not parts[0].isdigit():
        return None

    return {
        "序号":    parts[0] if len(parts) > 0 else "",
        "分类":    parts[1] if len(parts) > 1 else "",
        "原书名":  parts[2] if len(parts) > 2 else "",
        "状态":    parts[3] if len(parts) > 3 else "",
        "找到书名": parts[4] if len(parts) > 4 else "",
    }


def main():
    src = Path(INPUT_TXT)
    if not src.exists():
        print(f"❌ 找不到 {INPUT_TXT}")
        print("请把书单文本另存为 books_raw.txt（UTF-8 编码），再运行本脚本。")
        sys.exit(1)

    books = []
    skipped = 0
    with open(src, encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, 1):
            row = parse_line(line)
            if row:
                books.append(row)
            elif line.strip() and lineno > 1:
                skipped += 1

    if not books:
        print("❌ 未解析到任何数据，请检查文件格式。")
        sys.exit(1)

    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["序号", "分类", "原书名", "状态", "找到书名"])
        writer.writeheader()
        writer.writerows(books)

    not_found = sum(1 for b in books if "未找到" in b.get("状态", ""))
    already   = sum(1 for b in books if "找到"  in b.get("状态", "") and "未" not in b.get("状态", ""))

    print(f"✅ 已生成 {OUTPUT_CSV}")
    print(f"   总计：{len(books)} 条 | 已找到：{already} 条 | 待搜索：{not_found} 条")
    if skipped:
        print(f"   跳过无法解析的行：{skipped} 行")


if __name__ == "__main__":
    main()
