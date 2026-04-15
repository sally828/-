#!/usr/bin/env python3
"""
腾讯 IMA 知识库书单提取脚本（API 直连版）

使用方法：
1. 在 Chrome 打开 ima.qq.com，登录你的账号，进入知识库
2. F12 → Network → 找到 get_knowledge_list 请求 → 右键 → 复制 → 复制请求标头
3. 把 x-ima-cookie 和 x-ima-bkn 的值填到下面的配置里
4. 运行：python scrape_ima.py
"""

import requests
import csv
import json
from pathlib import Path

# ── 配置（从浏览器 DevTools 复制） ────────────────────────────────────────────
# F12 → Network → get_knowledge_list → 标头 → 请求标头

X_IMA_COOKIE = (
    "PLATFORM=H5; CLIENT-TYPE=256053; WEB-VERSION=999.999.999; "
    "IMA-GUID=guid-8e293d816e82f48deed2690802000001e1a317; "
    "IMA-Q36=8e293d816e82f48deed2690802000001e1a317; "
    "IMA-IUA=Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36; "
    "IMA-UID=001a24b36a00786d; "
    "IMA-TOKEN=+zK027Uk+jQmlMnx82r3KJ2m0FRGy7lzk01ptFtU/IBKVcSLZEox6+5ScLOgGQ9gwJl7xU520oeDLuhVbuLL1uTkVDvha0Hnlg6woI2+2dPTQpPJlpV5XjWZlM61i4KaUAizV41s/lUwSR/UnOQWLfddpfUeMmBEVgftmoXFaR10gw==; "
    "IMA-REFRESH-TOKEN=HDIeqnM/v+HqILRfAVZC8HdXqVRcB1d5w0k7WcEokjJa3QT4JOe+O5lPmmofZ8GpPX1uzSGoxmNVjC1xfpexTnXYPmykwEMNQP/D+B+BiXoApRLUDRdTpItawDi8PZKuRky6EwG/It5To9/PrGGkp9r+/9qFu0V6jEnMut1U7utKtJXdz54Kljj/YIavi6wVm+ZZszcf; "
    "UID-TYPE=2; TOKEN-TYPE=14"
)
X_IMA_BKN = "668892499"

KNOWLEDGE_BASE_ID = "7374371035301653"
OUTPUT_CSV = Path(__file__).parent / "ima_booklist.csv"
# ─────────────────────────────────────────────────────────────────────────────

API_URL = "https://ima.qq.com/cgi-bin/knowledge_tab_reader/get_knowledge_list"

HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "extension_version": "999.999.999",
    "from_browser_ima": "1",
    "x-ima-bkn": X_IMA_BKN,
    "x-ima-cookie": X_IMA_COOKIE,
    "referer": "https://ima.qq.com/wikis",
}

session = requests.Session()
session.headers.update(HEADERS)

books: list[dict] = []
seen_folders: set[str] = set()


def get_list(folder_id: str, cursor: str = "") -> dict:
    body = {
        "knowledge_base_id": KNOWLEDGE_BASE_ID,
        "folder_id": folder_id,
        "cursor": cursor,
        "limit": 50,
        "sort_type": 0,
        "need_default_cover": True,
        "ext_info": {},
        "version": "",
    }
    resp = session.post(API_URL, json=body, timeout=20)
    return resp.json()


def scan_folder(folder_id: str, l1: str = "", l2: str = ""):
    if folder_id in seen_folders:
        return
    seen_folders.add(folder_id)

    cursor = ""
    while True:
        data = get_list(folder_id, cursor)
        code = data.get("code", 0)
        if code != 0:
            print(f"  ⚠ folder {folder_id} 返回 code={code}: {data.get('msg','')}")
            break

        items = data.get("item_list") or data.get("items") or data.get("list") or []
        print(f"  📂 {l1 or 'root'}{'/'+l2 if l2 else ''}: {len(items)} 条")

        for item in items:
            name = (item.get("title") or item.get("name") or item.get("file_name") or "").strip()
            fid = item.get("folder_id") or ""
            if not name:
                continue
            if fid.startswith("folder_"):
                # 是文件夹，递归
                new_l1 = l1 or name
                new_l2 = (l2 or name) if l1 else ""
                scan_folder(fid, new_l1, new_l2)
            else:
                # 是文件
                books.append({
                    "一级分类": l1 or "根目录",
                    "二级分类": l2 or l1 or "根目录",
                    "书名": name,
                })

        cursor = data.get("next_cursor") or data.get("cursor") or ""
        if not cursor:
            break


def main():
    print("开始扫描 IMA 知识库...\n")

    # 先用已知的文件夹入口开始扫描
    known_folders = [
        "folder_7375431711882825",
        "folder_7434760334894700",
    ]

    for fid in known_folders:
        print(f"扫描入口: {fid}")
        scan_folder(fid)

    if not books:
        print("\n⚠ 未获取到数据。Token 可能已过期，请重新从浏览器复制 x-ima-cookie 和 x-ima-bkn")
        return

    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["一级分类", "二级分类", "书名"])
        writer.writeheader()
        writer.writerows(books)

    print(f"\n✅ 完成！共 {len(books)} 条，已保存到 {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
