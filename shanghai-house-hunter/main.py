#!/usr/bin/env python3
# =============================================================================
# main.py — 上海二手房数据分析系统 · 一键运行入口
# 提供命令行菜单，支持全量运行和各模块单独执行
# =============================================================================

import logging
import os
import sys

# ── 日志配置（同时输出到终端和 error.log）────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("error.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# 延迟导入业务模块（确保日志先初始化）
import config


# ─────────────────────────────────────────────
# 配置检查
# ─────────────────────────────────────────────

def check_config() -> bool:
    """
    检查必填配置项是否已填写。
    返回 True 表示配置完整，False 表示有缺失。
    """
    required = {
        "FEISHU_APP_ID":          config.FEISHU_APP_ID,
        "FEISHU_APP_SECRET":      config.FEISHU_APP_SECRET,
        "FEISHU_BASE_ID_HOUSE":   config.FEISHU_BASE_ID_HOUSE,
        "FEISHU_BASE_ID_REGION":  config.FEISHU_BASE_ID_REGION,
        "FEISHU_BASE_ID_KOL":     config.FEISHU_BASE_ID_KOL,
        "FEISHU_BASE_ID_SHORTLIST": config.FEISHU_BASE_ID_SHORTLIST,
        "FEISHU_TABLE_ID_HOUSE":  config.FEISHU_TABLE_ID_HOUSE,
        "FEISHU_TABLE_ID_REGION": config.FEISHU_TABLE_ID_REGION,
        "FEISHU_TABLE_ID_KOL":    config.FEISHU_TABLE_ID_KOL,
        "FEISHU_TABLE_ID_SHORTLIST": config.FEISHU_TABLE_ID_SHORTLIST,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print("\n⚠  以下配置项未填写，请先编辑 config.py：")
        for m in missing:
            print(f"   - {m}")
        return False

    # CLAUDE_API_KEY 非必须（KOL解析功能需要）
    if not config.CLAUDE_API_KEY:
        print("  ℹ  提示：CLAUDE_API_KEY 未填写，KOL文章解析功能将不可用")
    return True


# ─────────────────────────────────────────────
# 各功能模块
# ─────────────────────────────────────────────

def do_full_run():
    """全量运行：采集 → 评分 → 验证 → 输出看房清单。"""
    print("\n" + "=" * 60)
    print("  全量运行模式：采集 → 评分 → 交叉验证 → 看房清单")
    print("=" * 60)

    if not check_config():
        return

    import scraper_ke
    import scorer
    import validator

    # Step 1: 贝壳采集
    print("\n【Step 1/3】贝壳找房数据采集")
    try:
        total = scraper_ke.run_scraper()
        if total == 0:
            print("⚠ 未采集到数据，请检查网络或贝壳页面结构是否有变化")
            return
    except Exception as exc:
        logger.error("采集模块异常：%s", exc, exc_info=True)
        print(f"✗ 采集出错：{exc}")
        return

    # Step 2: 区域评分
    print("\n【Step 2/3】区域评分计算")
    try:
        scorer.run_scorer()
    except Exception as exc:
        logger.error("评分模块异常：%s", exc, exc_info=True)
        print(f"✗ 评分出错：{exc}（已记录，继续执行）")

    # Step 3: 交叉验证 + 看房清单
    print("\n【Step 3/3】交叉验证 + 生成看房清单")
    try:
        stats = validator.run_validator()
        print(f"\n✅ 全量运行完成！")
        print(f"   共处理房源：{stats['total']} 套")
        print(f"   入选看房清单：{stats['shortlisted']} 套")
        print(f"   ├─ 300-500万：{stats['low']} 套")
        print(f"   └─ 500-800万：{stats['high']} 套")
    except Exception as exc:
        logger.error("验证模块异常：%s", exc, exc_info=True)
        print(f"✗ 验证出错：{exc}")


def do_scrape_only():
    """仅运行贝壳数据采集。"""
    print("\n" + "=" * 60)
    print("  仅运行贝壳找房采集")
    print("=" * 60)

    if not check_config():
        return

    import scraper_ke

    # 询问是否指定区域
    print(f"\n默认采集区域：{', '.join(config.TARGET_DISTRICTS)}")
    custom = input("是否指定区域？（直接回车使用默认，或输入逗号分隔的区域名）: ").strip()
    if custom:
        districts = [d.strip() for d in custom.split(",") if d.strip()]
    else:
        districts = None

    try:
        total = scraper_ke.run_scraper(districts)
        print(f"\n✅ 采集完成，共写入 {total} 条")
    except Exception as exc:
        logger.error("采集出错：%s", exc, exc_info=True)
        print(f"✗ 出错：{exc}")


def do_kol_parse():
    """解析 KOL 文章（粘贴文本模式）。"""
    print("\n" + "=" * 60)
    print("  KOL 文章解析")
    print("=" * 60)

    if not config.CLAUDE_API_KEY:
        print("✗ 未配置 CLAUDE_API_KEY，请先在 config.py 中填写")
        return

    if not check_config():
        return

    import kol_parser

    print("\n请粘贴文章正文（粘贴完成后，在新的一行单独输入 END 并回车）：")
    print("-" * 40)
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)

    article_text = "\n".join(lines).strip()
    if not article_text:
        print("⚠ 未输入文章内容")
        return

    source = input("\n文章来源账号（可选，直接回车跳过）: ").strip()

    try:
        kol_parser.run_kol_parser(article_text, source)
    except Exception as exc:
        logger.error("KOL解析出错：%s", exc, exc_info=True)
        print(f"✗ 出错：{exc}")


def do_rescore():
    """重新计算区域评分和看房清单（不重新采集）。"""
    print("\n" + "=" * 60)
    print("  重新计算评分 + 看房清单")
    print("=" * 60)

    if not check_config():
        return

    import scorer
    import validator

    try:
        scorer.run_scorer()
        stats = validator.run_validator()
        print(f"\n✅ 重算完成！入选清单：{stats['shortlisted']} 套")
    except Exception as exc:
        logger.error("重算出错：%s", exc, exc_info=True)
        print(f"✗ 出错：{exc}")


def do_summary():
    """查看当前统计摘要（从飞书读取最新数据）。"""
    print("\n" + "=" * 60)
    print("  当前统计摘要")
    print("=" * 60)

    if not check_config():
        return

    import feishu_sdk

    try:
        # 01_房源总库 统计
        print("\n正在读取各表数据...")
        house_records = feishu_sdk.list_records(
            config.FEISHU_BASE_ID_HOUSE, config.FEISHU_TABLE_ID_HOUSE
        )
        kol_records = feishu_sdk.list_records(
            config.FEISHU_BASE_ID_KOL, config.FEISHU_TABLE_ID_KOL
        )
        shortlist_records = feishu_sdk.list_records(
            config.FEISHU_BASE_ID_SHORTLIST, config.FEISHU_TABLE_ID_SHORTLIST
        )

        print(f"\n{'─'*40}")
        print(f"  房源总库：      {len(house_records):>5} 条")
        print(f"  KOL观点库：     {len(kol_records):>5} 篇文章")
        print(f"  看房清单：      {len(shortlist_records):>5} 套精选")
        print(f"{'─'*40}")

        # 按价格区间分类统计
        if house_records:
            low_min, low_max   = config.BUDGET_LOW
            high_min, high_max = config.BUDGET_HIGH
            cnt_low  = sum(
                1 for r in house_records
                if low_min <= _to_float(r.get("fields", {}).get("总价(万元)", 0)) <= low_max
            )
            cnt_high = sum(
                1 for r in house_records
                if high_min <= _to_float(r.get("fields", {}).get("总价(万元)", 0)) <= high_max
            )
            print(f"  ├─ {low_min}-{low_max}万房源：  {cnt_low:>5} 套")
            print(f"  └─ {high_min}-{high_max}万房源：{cnt_high:>5} 套")

        # 区域分布
        if house_records:
            from collections import Counter
            district_cnt = Counter(
                r.get("fields", {}).get("所在区域", "未知")
                for r in house_records
            )
            print(f"\n  各区域房源数量：")
            for district, cnt in district_cnt.most_common():
                bar = "█" * min(cnt // 5, 20)
                print(f"    {district:<6} {cnt:>4} 套  {bar}")

        print(f"{'─'*40}")
        if os.path.exists("error.log"):
            size = os.path.getsize("error.log")
            print(f"  error.log 大小：{size} 字节")

    except Exception as exc:
        logger.error("读取摘要出错：%s", exc, exc_info=True)
        print(f"✗ 出错：{exc}")


def _to_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────
# 菜单主循环
# ─────────────────────────────────────────────

MENU = """
╔══════════════════════════════════════════════╗
║      上海二手房数据分析系统 · 主菜单          ║
╠══════════════════════════════════════════════╣
║  1. 全量运行（采集 → 评分 → 验证 → 输出）    ║
║  2. 仅运行贝壳数据采集                        ║
║  3. 解析 KOL 文章（粘贴文本）                 ║
║  4. 重新计算评分和看房清单                    ║
║  5. 查看当前统计摘要                          ║
║  0. 退出                                      ║
╚══════════════════════════════════════════════╝
"""

HANDLERS = {
    "1": do_full_run,
    "2": do_scrape_only,
    "3": do_kol_parse,
    "4": do_rescore,
    "5": do_summary,
}


def main():
    print("\n欢迎使用上海二手房数据分析系统 🏠")
    print("首次使用请先参阅 README.md 完成飞书配置")

    while True:
        print(MENU)
        choice = input("请输入选项（0-5）: ").strip()

        if choice == "0":
            print("\n再见！👋\n")
            break
        elif choice in HANDLERS:
            try:
                HANDLERS[choice]()
            except KeyboardInterrupt:
                print("\n\n⚠ 用户中断操作")
        else:
            print("⚠ 无效选项，请输入 0-5")


if __name__ == "__main__":
    main()
