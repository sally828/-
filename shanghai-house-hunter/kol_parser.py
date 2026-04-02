# =============================================================================
# kol_parser.py — KOL内容解析器（调用 Claude API）
# 接收用户粘贴的文章文本，提取关键信息，写入 03_KOL观点库
# 并更新 01_房源总库 中对应小区的 "KOL提及次数" 字段
# =============================================================================

import json
import logging
import re
from typing import Optional

import anthropic

import config
import feishu_sdk

logger = logging.getLogger(__name__)

# Claude 解析 prompt 模板
_PARSE_PROMPT = """\
你是一位专业的房产内容分析师。请仔细阅读以下文章，提取结构化信息并以 JSON 格式返回。

## 文章内容
{article}

## 提取要求
请提取以下字段，用 JSON 格式输出，不要有任何其他内容：
{{
  "communities": ["文章中明确提到的小区名列表，不要猜测，只提取有明确名称的"],
  "reasons": ["推荐理由或亮点的关键短句，每条不超过30字"],
  "risks": ["提到的风险、缺点或注意事项，每条不超过30字"],
  "is_ad": true 或 false,  // 判断是否疑似广告软文
  "ad_signals": ["引流信号列表，如：扫码加微信、点击链接、找我带看、私信我等"],
  "source": "文章的来源账号名或作者名，若无法判断填空字符串",
  "budget_range": "文章推荐的预算区间，如：300-500万、500-800万、不明确",
  "summary": "对文章核心观点的一句话总结，不超过50字"
}}

判断是否软文的依据：
- 出现引导加微信/扫码/私信等行为
- 只夸不贬，缺乏客观性
- 推荐集中在少数几个具体小区且过于详细
- 文末有中介信息或联系方式
"""


def parse_article(article_text: str, source_name: str = "") -> Optional[dict]:
    """
    调用 Claude API 解析 KOL 文章，提取结构化信息。

    参数：
        article_text：文章正文（纯文本）
        source_name：来源账号名（用户手动填写，可覆盖 Claude 识别结果）

    返回解析结果字典，失败返回 None。
    """
    if not article_text.strip():
        print("⚠ 文章内容为空，跳过解析")
        return None

    if not config.CLAUDE_API_KEY:
        print("✗ 未配置 CLAUDE_API_KEY，无法调用 Claude API")
        return None

    print("  → 正在调用 Claude API 解析文章...")
    client = anthropic.Anthropic(api_key=config.CLAUDE_API_KEY)

    # 限制文章长度，避免 token 超限（保留约 6000 字）
    MAX_ARTICLE_LEN = 6000
    if len(article_text) > MAX_ARTICLE_LEN:
        print(f"  ⚠ 文章过长（{len(article_text)}字），截断至 {MAX_ARTICLE_LEN} 字")
        article_text = article_text[:MAX_ARTICLE_LEN] + "...[截断]"

    prompt = _PARSE_PROMPT.format(article=article_text)

    try:
        message = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        print(f"  ✓ Claude 返回内容（{len(raw)} 字符）")
    except Exception as exc:
        logger.error("Claude API 调用失败：%s", exc)
        print(f"  ✗ Claude API 调用失败：{exc}")
        return None

    # 从响应中提取 JSON（有时 Claude 会加上 markdown 代码块）
    result = _extract_json(raw)
    if result is None:
        logger.error("无法解析 Claude 返回的 JSON：%s", raw[:200])
        print("  ✗ Claude 返回的内容不是有效 JSON，解析失败")
        return None

    # 如果用户手动提供了来源名，覆盖 Claude 识别的
    if source_name:
        result["source"] = source_name

    return result


def _extract_json(text: str) -> Optional[dict]:
    """
    从文本中提取 JSON 对象。
    兼容带 markdown 代码块（```json ... ```）和纯 JSON 两种格式。
    """
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试提取第一个 { ... } 块
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return None


# ─────────────────────────────────────────────
# 写入飞书 + 更新 01_房源总库
# ─────────────────────────────────────────────

def save_kol_result(parsed: dict) -> Optional[str]:
    """
    将解析结果写入飞书 03_KOL观点库。
    返回新建记录的 record_id。
    """
    # 列表类型字段需转为多行文本（飞书多行文本用换行分隔）
    communities_str = "\n".join(parsed.get("communities", []))
    reasons_str     = "\n".join(parsed.get("reasons", []))
    risks_str       = "\n".join(parsed.get("risks", []))
    ad_signals_str  = "\n".join(parsed.get("ad_signals", []))

    record = {
        "来源账号":   parsed.get("source", ""),
        "提及小区":   communities_str,
        "推荐理由":   reasons_str,
        "风险提示":   risks_str,
        "是否软文":   "是" if parsed.get("is_ad") else "否",
        "引流信号":   ad_signals_str,
        "适合预算":   parsed.get("budget_range", ""),
        "文章摘要":   parsed.get("summary", ""),
        "提及小区数": len(parsed.get("communities", [])),
    }

    print("  → 正在写入 03_KOL观点库...")
    ids = feishu_sdk.add_records(
        config.FEISHU_BASE_ID_KOL,
        config.FEISHU_TABLE_ID_KOL,
        [record],
    )
    return ids[0] if ids else None


def update_house_kol_mentions(communities: list[str]) -> int:
    """
    在 01_房源总库 中查找提及的小区，将其 "KOL提及次数" +1。
    返回更新的记录条数。
    """
    if not communities:
        return 0

    print(f"  → 正在更新 {len(communities)} 个小区的 KOL 提及次数...")
    # 读取所有房源记录（生产环境可加 filter 优化）
    records = feishu_sdk.list_records(
        config.FEISHU_BASE_ID_HOUSE,
        config.FEISHU_TABLE_ID_HOUSE,
    )

    updated = 0
    # 将小区名列表转为集合，加速匹配
    community_set = {c.strip() for c in communities if c.strip()}

    for rec in records:
        f = rec.get("fields", {})
        rec_community = str(f.get("小区名", "")).strip()
        if rec_community in community_set:
            current_mentions = int(f.get("KOL提及次数", 0) or 0)
            try:
                feishu_sdk.update_record(
                    config.FEISHU_BASE_ID_HOUSE,
                    config.FEISHU_TABLE_ID_HOUSE,
                    rec["record_id"],
                    {"KOL提及次数": current_mentions + 1},
                )
                updated += 1
            except Exception as exc:
                logger.error("更新小区 %s KOL提及次数失败：%s", rec_community, exc)

    return updated


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def run_kol_parser(article_text: str, source_name: str = "") -> bool:
    """
    完整执行 KOL 文章解析流程：
    1. 调用 Claude 解析文章
    2. 将结果写入 03_KOL观点库
    3. 更新 01_房源总库 中对应小区的 KOL 提及次数
    返回 True 表示成功。
    """
    print("\n开始解析 KOL 文章...")
    parsed = parse_article(article_text, source_name)
    if not parsed:
        print("✗ 文章解析失败")
        return False

    # 打印解析摘要
    print(f"\n── 解析结果 ──")
    print(f"  来源账号：{parsed.get('source', '未知')}")
    print(f"  提及小区：{', '.join(parsed.get('communities', [])) or '无'}")
    print(f"  推荐理由：{len(parsed.get('reasons', []))} 条")
    print(f"  风险提示：{len(parsed.get('risks', []))} 条")
    print(f"  是否软文：{'⚠ 疑似软文' if parsed.get('is_ad') else '✓ 非软文'}")
    if parsed.get("ad_signals"):
        print(f"  引流信号：{', '.join(parsed['ad_signals'])}")
    print(f"  适合预算：{parsed.get('budget_range', '不明确')}")
    print(f"  摘要：{parsed.get('summary', '')}")

    # 写入飞书
    record_id = save_kol_result(parsed)
    if record_id:
        print(f"  ✓ 已写入 03_KOL观点库（record_id: {record_id}）")
    else:
        print("  ✗ 写入 03_KOL观点库 失败")
        return False

    # 更新房源提及次数
    communities = parsed.get("communities", [])
    if communities:
        updated = update_house_kol_mentions(communities)
        print(f"  ✓ 已更新 {updated} 套房源的 KOL 提及次数")
    else:
        print("  → 文章未提及具体小区，跳过房源更新")

    print("\n✅ KOL 文章解析完成")
    return True
