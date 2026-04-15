#!/usr/bin/env python3
"""
IMA 桌面版本地数据探查脚本
扫描所有可能的存储位置，尝试提取书目数据

运行：python read_ima_local.py
输出：ima_scan.txt（所有发现，供分析）
"""

import os
import re
import json
import csv
from pathlib import Path

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
APPDATA      = os.environ.get("APPDATA", "")      # Roaming
HOME         = Path.home()

OUT_TXT = Path(__file__).parent / "ima_scan.txt"
OUT_CSV = Path(__file__).parent / "ima_booklist.csv"

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def extract_chinese(data: bytes) -> list[str]:
    """从字节中暴力提取中文字符串（书名通常是中文）"""
    found = set()

    for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
        try:
            text = data.decode(encoding, errors="replace")
        except Exception:
            continue
        # 3 个以上连续中文字符开头的片段（去掉控制字符）
        for m in re.finditer(
            r'[\u4e00-\u9fff\u3400-\u4dbf]{2,}[\u4e00-\u9fff\u3400-\u4dbf\w\s，。！？：；《》【】（）、…·—""'']{0,80}',
            text
        ):
            s = re.sub(r'\s+', ' ', m.group()).strip()
            if 3 <= len(s) <= 100:
                found.add(s)

    return sorted(found)


def try_parse_json(data: bytes):
    for enc in ("utf-8", "utf-8-sig", "utf-16"):
        try:
            return json.loads(data.decode(enc, errors="replace"))
        except Exception:
            pass
    return None


def file_size_str(sz: int) -> str:
    if sz >= 1024 * 1024:
        return f"{sz / 1024 / 1024:.1f} MB"
    if sz >= 1024:
        return f"{sz / 1024:.1f} KB"
    return f"{sz} B"


# ── 候选数据目录 ──────────────────────────────────────────────────────────────

def candidate_dirs() -> list[Path]:
    candidates = []
    for base in [LOCALAPPDATA, APPDATA, str(HOME / "AppData" / "Local"),
                 str(HOME / "AppData" / "Roaming")]:
        if not base:
            continue
        for name in ("ima.copilot", "ima", "IMA", "腾讯IMA", "tencent-ima"):
            p = Path(base) / name
            if p.exists():
                candidates.append(p)

    # Electron 默认 userData 路径可能在 AppData/Roaming/
    for base in [LOCALAPPDATA, APPDATA]:
        if base:
            for item in Path(base).iterdir() if Path(base).exists() else []:
                if "ima" in item.name.lower() and item.is_dir():
                    if item not in candidates:
                        candidates.append(item)

    return candidates


# ── 扫描 LevelDB 目录 ─────────────────────────────────────────────────────────

def scan_leveldb(dirpath: Path, report: list) -> list[str]:
    report.append(f"\n{'─'*55}")
    report.append(f"LevelDB: {dirpath}")
    found_text = []

    if not dirpath.exists():
        report.append("  [不存在]")
        return found_text

    for f in sorted(dirpath.iterdir()):
        if not f.is_file():
            continue
        sz = f.stat().st_size
        report.append(f"  {f.name:<44} {file_size_str(sz):>8}")

        if sz == 0 or f.suffix.lower() not in (".ldb", ".log", ".sst"):
            continue

        try:
            data = f.read_bytes()
            texts = extract_chinese(data)
            if texts:
                report.append(f"    ↳ 提取到 {len(texts)} 个中文片段：")
                for t in texts[:40]:
                    report.append(f"      · {t}")
                found_text.extend(texts)
        except Exception as e:
            report.append(f"    ↳ 读取失败：{e}")

    return found_text


# ── 扫描通用目录（递归，深度限制） ────────────────────────────────────────────

def scan_dir(dirpath: Path, report: list, depth: int = 0,
             max_depth: int = 6, max_file_size: int = 30_000_000) -> list[str]:
    if depth > max_depth or not dirpath.exists():
        return []

    indent = "  " * depth
    found_text = []

    try:
        items = sorted(dirpath.iterdir(), key=lambda x: (x.is_file(), x.name))
    except PermissionError:
        report.append(f"{indent}[权限不足]")
        return found_text

    for item in items:
        try:
            if item.is_dir():
                total_sz = sum(f.stat().st_size for f in item.rglob("*")
                               if f.is_file())
                report.append(f"{indent}📁 {item.name}/  [{file_size_str(total_sz)}]")
                sub = scan_dir(item, report, depth + 1, max_depth, max_file_size)
                found_text.extend(sub)
                continue

            sz = item.stat().st_size
            ext = item.suffix.lower()
            report.append(f"{indent}📄 {item.name:<50} {file_size_str(sz):>8}")

            if sz == 0 or sz > max_file_size:
                continue

            data = item.read_bytes()

            # JSON 文件：尝试结构化解析
            if ext == ".json":
                j = try_parse_json(data)
                if j is not None:
                    report.append(f"{indent}   → JSON ({type(j).__name__})")
                    # 递归提取字符串值
                    flat = json.dumps(j, ensure_ascii=False)
                    texts = extract_chinese(flat.encode("utf-8"))
                    if texts:
                        report.append(f"{indent}   → 中文片段 {len(texts)} 个：")
                        for t in texts[:30]:
                            report.append(f"{indent}     · {t}")
                        found_text.extend(texts)
                    continue

            # 其余文件：暴力提取
            if ext in (".ldb", ".log", ".sst", ".db", ".sqlite",
                       ".dat", ".bin", ".data", ""):
                texts = extract_chinese(data)
                if texts:
                    report.append(f"{indent}   → 中文片段 {len(texts)} 个：")
                    for t in texts[:25]:
                        report.append(f"{indent}     · {t}")
                    found_text.extend(texts)

        except Exception as e:
            report.append(f"{indent}   [读取错误：{e}]")

    return found_text


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    report = [
        "=" * 60,
        "IMA 桌面版数据探查报告",
        "=" * 60,
        "",
    ]

    dirs = candidate_dirs()
    if not dirs:
        report.append("❌ 未找到任何 IMA 数据目录！")
        report.append(f"   搜索过：{LOCALAPPDATA}, {APPDATA}")
        OUT_TXT.write_text("\n".join(report), encoding="utf-8")
        print("\n".join(report))
        return

    report.append(f"找到 {len(dirs)} 个 IMA 数据目录：")
    for d in dirs:
        report.append(f"  {d}")
    report.append("")

    all_texts: list[str] = []

    for base_dir in dirs:
        report.append(f"\n{'='*60}")
        report.append(f"扫描根目录：{base_dir}")
        report.append(f"{'='*60}")

        # Chromium-style User Data
        user_data = base_dir / "User Data" / "Default"
        if user_data.exists():
            # IndexedDB
            idb_root = user_data / "IndexedDB"
            if idb_root.exists():
                for db_dir in sorted(idb_root.glob("*.leveldb")):
                    texts = scan_leveldb(db_dir, report)
                    all_texts.extend(texts)

            # Local Storage
            ls_dir = user_data / "Local Storage" / "leveldb"
            if ls_dir.exists():
                texts = scan_leveldb(ls_dir, report)
                all_texts.extend(texts)

            # 扫描 Default 下所有非标准 Chromium 目录
            report.append(f"\n── Default 目录内容 ──")
            standard_dirs = {
                "IndexedDB", "Local Storage", "Session Storage",
                "Cache", "Code Cache", "GPUCache", "Logs",
                "blob_storage", "databases", "Extension State",
                "Network Persistent State", "Service Worker",
                "Web Data",
            }
            for item in sorted(user_data.iterdir()):
                if item.is_dir() and item.name not in standard_dirs:
                    report.append(f"\n── 非标准目录（可能是 IMA 自定义存储）：{item.name} ──")
                    texts = scan_dir(item, report)
                    all_texts.extend(texts)
        else:
            # 直接扫描
            texts = scan_dir(base_dir, report)
            all_texts.extend(texts)

    # ── 汇总 ────────────────────────────────────────────────────────────────
    unique = list(dict.fromkeys(
        t.strip() for t in all_texts
        if t.strip() and len(t.strip()) >= 3
    ))

    report.append(f"\n\n{'='*60}")
    report.append(f"汇总：共提取到 {len(unique)} 个不重复的中文文本片段")
    report.append(f"{'='*60}")
    for t in unique[:200]:
        report.append(f"  {t}")

    result_text = "\n".join(report)
    OUT_TXT.write_text(result_text, encoding="utf-8")

    # 打印前 50 行供参考
    for line in report[:80]:
        print(line)
    print(f"\n... 完整结果已保存到：{OUT_TXT}")
    print()
    print("📋 请把 ima_scan.txt 发给我，我来判断哪些是书名并生成书单。")
    print("   如果文件太大，可以只发前 500 行：")
    print("   Get-Content ima_scan.txt -TotalCount 500 | Set-Content ima_scan_top.txt")


if __name__ == "__main__":
    main()
