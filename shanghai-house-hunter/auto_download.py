#!/usr/bin/env python3
# =============================================================================
# auto_download.py — 上海二手房数据分析系统 · 自动采集入口
#
# 用法（可从任意目录运行）：
#   python auto_download.py
#   python auto_download.py --districts 浦东,徐汇
#   python auto_download.py --rescore-only
#
# 与 main.py 的区别：无交互菜单，直接执行全量采集 → 评分 → 验证流程，
# 适合计划任务（Windows 任务计划程序 / cron）定时自动运行。
# =============================================================================

import argparse
import logging
import os
import sys

# ── 路径修复：将本脚本所在目录加入 sys.path ──────────────────────────────────
# 无论从哪个目录调用此脚本（如 C:\Users\Sally），都能正确导入项目模块。
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# ── 日志配置 ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(_SCRIPT_DIR, "error.log"), encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

import config  # noqa: E402  （延迟到 sys.path 修复之后导入）


# ─────────────────────────────────────────────
# 状态打印辅助
# ─────────────────────────────────────────────

def _banner(text: str) -> None:
    width = 60
    print("\n" + "=" * width)
    print(f"  {text}")
    print("=" * width)


def _status(step: int, total: int, text: str) -> None:
    print(f"\n【Step {step}/{total}】{text}")


# ─────────────────────────────────────────────
# 配置检查
# ─────────────────────────────────────────────

def _check_config() -> bool:
    required = {
        "FEISHU_APP_ID":            config.FEISHU_APP_ID,
        "FEISHU_APP_SECRET":        config.FEISHU_APP_SECRET,
        "FEISHU_BASE_ID_HOUSE":     config.FEISHU_BASE_ID_HOUSE,
        "FEISHU_BASE_ID_REGION":    config.FEISHU_BASE_ID_REGION,
        "FEISHU_BASE_ID_KOL":       config.FEISHU_BASE_ID_KOL,
        "FEISHU_BASE_ID_SHORTLIST": config.FEISHU_BASE_ID_SHORTLIST,
        "FEISHU_TABLE_ID_HOUSE":    config.FEISHU_TABLE_ID_HOUSE,
        "FEISHU_TABLE_ID_REGION":   config.FEISHU_TABLE_ID_REGION,
        "FEISHU_TABLE_ID_KOL":      config.FEISHU_TABLE_ID_KOL,
        "FEISHU_TABLE_ID_SHORTLIST": config.FEISHU_TABLE_ID_SHORTLIST,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print("\n⚠  以下配置项未填写，请先编辑 config.py：")
        for m in missing:
            print(f"   - {m}")
        print(f"\n   config.py 路径：{os.path.join(_SCRIPT_DIR, 'config.py')}")
        return False
    if not config.CLAUDE_API_KEY:
        print("  ℹ  提示：CLAUDE_API_KEY 未填写，KOL文章解析功能不可用")
    return True


# ─────────────────────────────────────────────
# 全量采集流程
# ─────────────────────────────────────────────

def run_full(districts: list[str] | None = None) -> int:
    """
    执行完整的下载 → 评分 → 验证流程。
    返回退出码（0 = 成功，1 = 出错）。
    """
    _banner("上海二手房数据分析系统 · 自动采集")
    print(f"  项目目录：{_SCRIPT_DIR}")
    if districts:
        print(f"  目标区域：{', '.join(districts)}")
    else:
        print(f"  目标区域：{', '.join(config.TARGET_DISTRICTS)}（默认）")

    if not _check_config():
        return 1

    import scraper_ke
    import scorer
    import validator

    # ── Step 1：贝壳数据采集 ──────────────────────────────────────────────
    _status(1, 3, "贝壳找房数据采集")
    try:
        total = scraper_ke.run_scraper(districts)
    except Exception as exc:
        logger.error("采集模块异常：%s", exc, exc_info=True)
        print(f"✗ 采集出错：{exc}")
        return 1

    if total == 0:
        print("⚠  未采集到数据，请检查网络或贝壳页面结构是否有变化")
        return 1

    print(f"  ✓ 采集完成，共写入 {total} 条")

    # ── Step 2：区域评分 ──────────────────────────────────────────────────
    _status(2, 3, "区域评分计算")
    try:
        scorer.run_scorer()
        print("  ✓ 评分完成")
    except Exception as exc:
        logger.error("评分模块异常：%s", exc, exc_info=True)
        print(f"  ✗ 评分出错：{exc}（已记录，继续执行）")

    # ── Step 3：交叉验证 + 看房清单 ───────────────────────────────────────
    _status(3, 3, "交叉验证 + 生成看房清单")
    try:
        stats = validator.run_validator()
    except Exception as exc:
        logger.error("验证模块异常：%s", exc, exc_info=True)
        print(f"  ✗ 验证出错：{exc}")
        return 1

    _banner("全量采集完成")
    print(f"  共处理房源：    {stats['total']:>5} 套")
    print(f"  入选看房清单：  {stats['shortlisted']:>5} 套")
    print(f"    ├─ 300-500万：{stats['low']:>5} 套")
    print(f"    └─ 500-800万：{stats['high']:>5} 套")
    print()
    return 0


# ─────────────────────────────────────────────
# 仅重算（不重新采集）
# ─────────────────────────────────────────────

def run_rescore_only() -> int:
    """重新计算评分和看房清单，不重新采集数据。"""
    _banner("上海二手房数据分析系统 · 重算评分")

    if not _check_config():
        return 1

    import scorer
    import validator

    _status(1, 2, "区域评分计算")
    try:
        scorer.run_scorer()
        print("  ✓ 评分完成")
    except Exception as exc:
        logger.error("重算评分异常：%s", exc, exc_info=True)
        print(f"  ✗ 评分出错：{exc}")
        return 1

    _status(2, 2, "交叉验证 + 生成看房清单")
    try:
        stats = validator.run_validator()
    except Exception as exc:
        logger.error("重算验证异常：%s", exc, exc_info=True)
        print(f"  ✗ 验证出错：{exc}")
        return 1

    _banner("重算完成")
    print(f"  入选看房清单：  {stats['shortlisted']:>5} 套")
    print(f"    ├─ 300-500万：{stats['low']:>5} 套")
    print(f"    └─ 500-800万：{stats['high']:>5} 套")
    print()
    return 0


# ─────────────────────────────────────────────
# 命令行入口
# ─────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="上海二手房数据分析系统 · 自动采集脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python auto_download.py                         # 全量采集（默认区域）
  python auto_download.py --districts 浦东,徐汇   # 只采集指定区域
  python auto_download.py --rescore-only          # 不采集，仅重算评分
        """,
    )
    parser.add_argument(
        "--districts",
        metavar="区域1,区域2",
        help="指定采集的区域，逗号分隔（默认使用 config.py 中的 TARGET_DISTRICTS）",
    )
    parser.add_argument(
        "--rescore-only",
        action="store_true",
        help="跳过采集，仅重新计算评分和看房清单",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.rescore_only:
        sys.exit(run_rescore_only())

    districts = None
    if args.districts:
        districts = [d.strip() for d in args.districts.split(",") if d.strip()]

    sys.exit(run_full(districts))


if __name__ == "__main__":
    main()
