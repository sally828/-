# =============================================================================
# scorer.py — 区域评分计算器
# 从飞书 01_房源总库 读取数据，按区域聚合统计，
# 加权计算综合评分（满分100），结果写入 02_区域评分表
# =============================================================================

import logging
import statistics
from collections import defaultdict
from typing import Any

import config
import feishu_sdk

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据聚合
# ─────────────────────────────────────────────

def _aggregate_by_district(records: list[dict]) -> dict[str, dict[str, Any]]:
    """
    将房源记录按区域聚合，计算各项统计指标。
    返回：{区域名: {指标key: 值, ...}}
    """
    buckets: dict[str, dict[str, list]] = defaultdict(lambda: {
        "prices":          [],  # 总价列表（万元）
        "unit_prices":     [],  # 单价列表（元/㎡）
        "listing_days":    [],  # 挂牌天数列表
        "attention":       [],  # 关注人数列表
        "price_drop_cnt":  0,   # 降价房源数（用 int 存）
        "total_cnt":       0,   # 总房源数
        "kol_mentions":    [],  # KOL提及次数列表
    })

    for rec in records:
        f = rec.get("fields", rec)  # 兼容飞书返回格式和本地字典格式
        district = str(f.get("所在区域", "")).strip()
        if not district:
            continue

        b = buckets[district]
        b["total_cnt"] += 1

        price = _to_float(f.get("总价(万元)", 0))
        if price > 0:
            b["prices"].append(price)

        unit = _to_float(f.get("单价(元/㎡)", 0))
        if unit > 0:
            b["unit_prices"].append(unit)

        days = _to_int(f.get("挂牌天数", 0))
        if days > 0:
            b["listing_days"].append(days)

        att = _to_int(f.get("关注人数", 0))
        b["attention"].append(att)

        if str(f.get("是否降价", "否")).strip() == "是":
            b["price_drop_cnt"] += 1

        kol = _to_int(f.get("KOL提及次数", 0))
        b["kol_mentions"].append(kol)

    # 整理成干净的统计字典
    stats: dict[str, dict[str, Any]] = {}
    for district, b in buckets.items():
        total = b["total_cnt"]
        stats[district] = {
            "房源数量":         total,
            "均价(万元)":       round(_safe_mean(b["prices"]), 2),
            "均价(元/㎡)":      round(_safe_mean(b["unit_prices"])),
            "挂牌天数中位数":   round(_safe_median(b["listing_days"])),
            "关注人数均值":     round(_safe_mean(b["attention"]), 1),
            "降价率(%)":        round(b["price_drop_cnt"] / total * 100, 1) if total else 0,
            "KOL提及均值":      round(_safe_mean(b["kol_mentions"]), 2),
        }

    return stats


# ─────────────────────────────────────────────
# 评分计算
# ─────────────────────────────────────────────

def _normalize(value: float, min_val: float, max_val: float, invert: bool = False) -> float:
    """
    将 value 线性归一化到 [0, 100]。
    invert=True 表示越小越好（如均价、挂牌天数）。
    """
    if max_val == min_val:
        return 50.0  # 无法区分时给中间值
    ratio = (value - min_val) / (max_val - min_val)
    score = (1 - ratio) * 100 if invert else ratio * 100
    return max(0.0, min(100.0, score))


def _calc_scores(stats: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    对各区域的原始统计指标进行归一化评分，加权汇总成综合分。
    返回包含评分字段的新字典。
    """
    if not stats:
        return {}

    # 提取各指标的全局极值（用于归一化）
    all_avg_price   = [v["均价(万元)"]       for v in stats.values()]
    all_days        = [v["挂牌天数中位数"]    for v in stats.values()]
    all_attention   = [v["关注人数均值"]      for v in stats.values()]
    all_drop_rate   = [v["降价率(%)"]         for v in stats.values()]
    all_kol         = [v["KOL提及均值"]       for v in stats.values()]

    scored: dict[str, dict[str, Any]] = {}

    for district, s in stats.items():
        # 均价竞争力：均价越低，分越高（invert=True）
        avg_price_score = _normalize(
            s["均价(万元)"], min(all_avg_price), max(all_avg_price), invert=True
        )
        # 成交速度分：挂牌天数越短越好（invert=True）
        speed_score = _normalize(
            s["挂牌天数中位数"], min(all_days), max(all_days), invert=True
        )
        # 热度分：关注人数越多越好
        hotness_score = _normalize(
            s["关注人数均值"], min(all_attention), max(all_attention)
        )
        # 降价率分：降价率越低越好（invert=True）
        price_drop_score = _normalize(
            s["降价率(%)"], min(all_drop_rate), max(all_drop_rate), invert=True
        )
        # KOL提及分：提及次数越多越好
        kol_score = _normalize(
            s["KOL提及均值"], min(all_kol), max(all_kol)
        )

        # 加权综合分
        w = config.SCORE_WEIGHTS
        composite = (
            avg_price_score   * w["avg_price_score"]
            + speed_score     * w["speed_score"]
            + hotness_score   * w["hotness_score"]
            + price_drop_score * w["price_drop_score"]
            + kol_score       * w["kol_mention_score"]
        )

        scored[district] = {
            **s,
            "均价竞争力分":   round(avg_price_score, 1),
            "成交速度分":     round(speed_score, 1),
            "热度分":         round(hotness_score, 1),
            "价格稳定分":     round(price_drop_score, 1),
            "KOL热度分":      round(kol_score, 1),
            "综合评分":       round(composite, 1),
        }

    return scored


def _rank(scored: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """按综合评分降序排列，添加排名字段。"""
    items = sorted(scored.items(), key=lambda x: x[1]["综合评分"], reverse=True)
    result = []
    for rank, (district, data) in enumerate(items, start=1):
        result.append({"区域名称": district, "排名": rank, **data})
    return result


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def run_scorer() -> list[dict]:
    """
    从飞书读取房源数据，计算区域评分，写入 02_区域评分表。
    返回排名后的评分列表（供 main.py 展示摘要）。
    """
    print("\n读取 01_房源总库 中的房源数据...")
    records = feishu_sdk.list_records(
        config.FEISHU_BASE_ID_HOUSE,
        config.FEISHU_TABLE_ID_HOUSE,
    )

    if not records:
        print("⚠ 房源总库为空，请先运行贝壳采集器")
        return []

    print(f"✓ 读取 {len(records)} 条房源，开始聚合计算...")
    stats   = _aggregate_by_district(records)
    scored  = _calc_scores(stats)
    ranked  = _rank(scored)

    print(f"\n区域综合评分排行（Top {len(ranked)}）：")
    print(f"{'排名':<4} {'区域':<8} {'综合分':<8} {'均价(万)':<10} {'挂牌天数中位':<14} {'降价率':<8}")
    print("-" * 56)
    for row in ranked:
        print(
            f"{row['排名']:<4} {row['区域名称']:<8} {row['综合评分']:<8} "
            f"{row['均价(万元)']:<10} {row['挂牌天数中位数']:<14} {row['降价率(%)']:<8}%"
        )

    # 写入飞书 02_区域评分表（先清空旧数据再写入，简单粗暴但可靠）
    print("\n正在将评分结果写入 02_区域评分表...")
    # 读取旧记录 ID 并删除
    old_records = feishu_sdk.list_records(
        config.FEISHU_BASE_ID_REGION,
        config.FEISHU_TABLE_ID_REGION,
    )
    for old in old_records:
        try:
            feishu_sdk.delete_record(
                config.FEISHU_BASE_ID_REGION,
                config.FEISHU_TABLE_ID_REGION,
                old["record_id"],
            )
        except Exception as exc:
            logger.warning("删除旧评分记录失败：%s", exc)

    # 写入新评分
    # 飞书字段需要和表格字段名一一对应，确保 README 中字段名与此一致
    feishu_records = [
        {
            "区域名称":       row["区域名称"],
            "排名":           row["排名"],
            "综合评分":       row["综合评分"],
            "均价(万元)":     row["均价(万元)"],
            "均价(元/㎡)":    row["均价(元/㎡)"],
            "挂牌天数中位数": row["挂牌天数中位数"],
            "关注人数均值":   row["关注人数均值"],
            "降价率(%)":      row["降价率(%)"],
            "KOL提及均值":    row["KOL提及均值"],
            "均价竞争力分":   row["均价竞争力分"],
            "成交速度分":     row["成交速度分"],
            "热度分":         row["热度分"],
            "价格稳定分":     row["价格稳定分"],
            "KOL热度分":      row["KOL热度分"],
            "房源数量":       row["房源数量"],
        }
        for row in ranked
    ]
    ids = feishu_sdk.add_records(
        config.FEISHU_BASE_ID_REGION,
        config.FEISHU_TABLE_ID_REGION,
        feishu_records,
    )
    print(f"✅ 区域评分完成，共写入 {len(ids)} 个区域的数据")
    return ranked


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def _to_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _to_int(val: Any, default: int = 0) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def _safe_mean(lst: list) -> float:
    return statistics.mean(lst) if lst else 0.0


def _safe_median(lst: list) -> float:
    return statistics.median(lst) if lst else 0.0
