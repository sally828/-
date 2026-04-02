# =============================================================================
# validator.py — 交叉验证 + 可信度评分 + 看房清单生成
# 对 01_房源总库 每套房计算综合可信度分（0-100），
# 分 ≥ SHORTLIST_MIN_SCORE 的房源自动推送到 04_看房清单
# =============================================================================

import logging
from typing import Any

import config
import feishu_sdk

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 可信度评分规则（加减分制，基础分 50）
# ─────────────────────────────────────────────
BASE_SCORE = 50


def _calc_trust_score(f: dict) -> tuple[int, list[str]]:
    """
    根据单套房源的字段计算可信度评分。

    参数 f：飞书返回的 fields 字典（字段名 → 值）
    返回 (score, 评分说明列表)
    """
    score = BASE_SCORE
    notes: list[str] = []

    # ── KOL 提及加分 ──────────────────────────────────────────────────────────
    kol_cnt = _to_int(f.get("KOL提及次数", 0))
    if kol_cnt >= 3:
        score += 30
        notes.append(f"KOL提及 {kol_cnt} 次 (+30)")
    elif kol_cnt == 2:
        score += 20
        notes.append(f"KOL提及 {kol_cnt} 次 (+20)")
    elif kol_cnt == 1:
        score += 10
        notes.append(f"KOL提及 {kol_cnt} 次 (+10)")

    # ── 挂牌时长扣分 ──────────────────────────────────────────────────────────
    listing_days = _to_int(f.get("挂牌天数", 0))
    if listing_days > 180:
        score -= 20
        notes.append(f"挂牌 {listing_days} 天 > 180天 (-20)")
    elif listing_days > 90:
        score -= 10
        notes.append(f"挂牌 {listing_days} 天 > 90天 (-10)")

    # ── 降价信号扣分 ──────────────────────────────────────────────────────────
    if str(f.get("是否降价", "否")).strip() == "是":
        score -= 15
        notes.append("已降价 (-15)")

    # ── 关注热度加分 ──────────────────────────────────────────────────────────
    attention = _to_int(f.get("关注人数", 0))
    if attention > 100:
        score += 10
        notes.append(f"关注人数 {attention} > 100 (+10)")

    # ── 软文来源扣分（暂时通过 KOL 提及为 0 && 挂牌很新 判断，正式版可关联 KOL 表）──
    # 此处预留接口：若该小区的所有 KOL 来源均为软文，扣 20 分
    # 目前通过字段 "软文标记" 判断（由 kol_parser 更新，默认 0）
    ad_flag = _to_int(f.get("软文标记", 0))
    if ad_flag >= 1:
        score -= 20
        notes.append("来源疑似软文 (-20)")

    # 分数限制在 [0, 100]
    score = max(0, min(100, score))
    return score, notes


# ─────────────────────────────────────────────
# 优先级标注
# ─────────────────────────────────────────────

def _get_priority(score: int) -> str:
    """根据综合分返回优先级标签。"""
    if score >= 85:
        return "⭐⭐⭐ 强烈推荐"
    elif score >= 70:
        return "⭐⭐ 值得看"
    else:
        return "⭐ 备选"


def _get_budget_label(total_price: float) -> str:
    """根据总价返回预算档位标签。"""
    low_min, low_max   = config.BUDGET_LOW
    high_min, high_max = config.BUDGET_HIGH
    if low_min <= total_price <= low_max:
        return f"{low_min}-{low_max}万"
    elif high_min <= total_price <= high_max:
        return f"{high_min}-{high_max}万"
    else:
        return "其他"


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def run_validator() -> dict[str, int]:
    """
    读取 01_房源总库，对每套房计算可信度评分，
    更新 01_房源总库 的 "可信度评分" 字段，
    并将高分房源写入 04_看房清单。

    返回统计摘要字典。
    """
    print("\n读取 01_房源总库 中的房源数据...")
    records = feishu_sdk.list_records(
        config.FEISHU_BASE_ID_HOUSE,
        config.FEISHU_TABLE_ID_HOUSE,
    )

    if not records:
        print("⚠ 房源总库为空，请先运行贝壳采集器")
        return {"total": 0, "shortlisted": 0, "low": 0, "high": 0}

    print(f"✓ 读取 {len(records)} 条房源，开始可信度评分...")

    shortlist_low: list[dict]  = []  # 300-500万 清单
    shortlist_high: list[dict] = []  # 500-800万 清单
    updated_cnt = 0

    for i, rec in enumerate(records, start=1):
        f = rec.get("fields", {})
        record_id = rec.get("record_id", "")

        score, notes = _calc_trust_score(f)

        # 更新 01_房源总库 的可信度评分（批量更新性能更好，但飞书暂无 batch_update 接口）
        try:
            feishu_sdk.update_record(
                config.FEISHU_BASE_ID_HOUSE,
                config.FEISHU_TABLE_ID_HOUSE,
                record_id,
                {"可信度评分": score},
            )
            updated_cnt += 1
        except Exception as exc:
            logger.error("更新记录 %s 可信度评分失败：%s", record_id, exc)

        if i % 50 == 0:
            print(f"  → 已处理 {i}/{len(records)} 条...")

        # 判断是否入选看房清单
        if score < config.SHORTLIST_MIN_SCORE:
            continue

        total_price = _to_float(f.get("总价(万元)", 0))
        budget_label = _get_budget_label(total_price)

        shortlist_item = {
            "小区名":     str(f.get("小区名", "")),
            "所在区域":   str(f.get("所在区域", "")),
            "板块":       str(f.get("板块", "")),
            "总价(万元)": total_price,
            "单价(元/㎡)": _to_int(f.get("单价(元/㎡)", 0)),
            "面积(㎡)":   _to_float(f.get("面积(㎡)", 0)),
            "房型":       str(f.get("房型", "")),
            "楼层":       str(f.get("楼层", "")),
            "建造年份":   _to_int(f.get("建造年份", 0)),
            "挂牌天数":   _to_int(f.get("挂牌天数", 0)),
            "是否降价":   str(f.get("是否降价", "否")),
            "关注人数":   _to_int(f.get("关注人数", 0)),
            "KOL提及次数": _to_int(f.get("KOL提及次数", 0)),
            "可信度评分": score,
            "评分说明":   " | ".join(notes) if notes else "基础分",
            "预算档位":   budget_label,
            "优先级":     _get_priority(score),
            "页面链接":   str(f.get("页面链接", "")),
        }

        low_min, low_max   = config.BUDGET_LOW
        high_min, high_max = config.BUDGET_HIGH
        if low_min <= total_price <= low_max:
            shortlist_low.append(shortlist_item)
        elif high_min <= total_price <= high_max:
            shortlist_high.append(shortlist_item)

    # 按可信度评分降序排序
    shortlist_low.sort(key=lambda x: x["可信度评分"], reverse=True)
    shortlist_high.sort(key=lambda x: x["可信度评分"], reverse=True)
    all_shortlist = shortlist_low + shortlist_high

    print(f"\n  ✓ 可信度评分更新完成（{updated_cnt} 条）")
    print(f"  ✓ 入选看房清单：{len(shortlist_low)} 套（300-500万）+ {len(shortlist_high)} 套（500-800万）")

    # 清空旧的看房清单再写入新的
    print("\n正在更新 04_看房清单...")
    old_shortlist = feishu_sdk.list_records(
        config.FEISHU_BASE_ID_SHORTLIST,
        config.FEISHU_TABLE_ID_SHORTLIST,
    )
    for old in old_shortlist:
        try:
            feishu_sdk.delete_record(
                config.FEISHU_BASE_ID_SHORTLIST,
                config.FEISHU_TABLE_ID_SHORTLIST,
                old["record_id"],
            )
        except Exception as exc:
            logger.warning("删除旧清单记录失败：%s", exc)

    if all_shortlist:
        ids = feishu_sdk.add_records(
            config.FEISHU_BASE_ID_SHORTLIST,
            config.FEISHU_TABLE_ID_SHORTLIST,
            all_shortlist,
        )
        print(f"✅ 看房清单更新完成，共 {len(ids)} 套房源入选")
    else:
        print("⚠ 没有房源达到入选阈值（可信度 ≥ 60），清单为空")

    stats = {
        "total":       len(records),
        "shortlisted": len(all_shortlist),
        "low":         len(shortlist_low),
        "high":        len(shortlist_high),
    }

    # 打印清单预览
    _print_shortlist_preview(shortlist_low, shortlist_high)
    return stats


def _print_shortlist_preview(low: list[dict], high: list[dict]) -> None:
    """打印看房清单预览（每档前5条）。"""
    for label, lst in [("300-500万", low), ("500-800万", high)]:
        print(f"\n── {label} 精选清单（前5套）──")
        if not lst:
            print("  （无）")
            continue
        print(f"{'优先级':<16} {'小区名':<16} {'区域':<6} {'总价':<8} {'可信度':<8} {'KOL'}")
        print("-" * 70)
        for item in lst[:5]:
            print(
                f"{item['优先级']:<16} {item['小区名']:<16} {item['所在区域']:<6} "
                f"{item['总价(万元)']}万  {item['可信度评分']:<8} "
                f"提及{item['KOL提及次数']}次"
            )


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def _to_int(val: Any, default: int = 0) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def _to_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default
