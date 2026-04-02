# =============================================================================
# scraper_ke.py — 贝壳找房上海二手房采集器
# 采集 https://sh.ke.com/ershoufang/ 按区域 + 价格区间筛选
# 遵守 robots.txt，加随机延迟，自动写入飞书 01_房源总库
# =============================================================================

import re
import time
import random
import logging
import urllib.robotparser
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

import config
import feishu_sdk

logger = logging.getLogger(__name__)

# 贝壳各行政区对应的 URL 路径关键字
# 格式：显示名 → URL 路径段
DISTRICT_URL_MAP = {
    "浦东": "pudong",
    "徐汇": "xuhui",
    "普陀": "putuo",
    "长宁": "changning",
    "闵行": "minhang",
    "杨浦": "yangpu",
    "虹口": "hongkou",
    "静安": "jingan",
    "黄浦": "huangpu",
    "宝山": "baoshan",
    "嘉定": "jiading",
    "松江": "songjiang",
}

# 价格区间对应的贝壳 URL 参数
# 贝壳价格筛选格式：bp300ep500（300万-500万）
PRICE_RANGE_MAP = {
    "300-500万": "bp300ep500",
    "500-800万": "bp500ep800",
}


# ─────────────────────────────────────────────
# robots.txt 检查
# ─────────────────────────────────────────────

def _check_robots_allowed(url: str) -> bool:
    """
    检查目标 URL 是否允许爬取（遵守 robots.txt）。
    若无法获取 robots.txt，默认允许（宽松策略）。
    """
    try:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = "https://sh.ke.com/robots.txt"
        rp.set_url(robots_url)
        rp.read()
        allowed = rp.can_fetch("*", url)
        if not allowed:
            logger.warning("robots.txt 禁止访问：%s", url)
        return allowed
    except Exception as exc:
        logger.warning("无法读取 robots.txt（%s），跳过检查", exc)
        return True


# ─────────────────────────────────────────────
# 页面请求
# ─────────────────────────────────────────────

def _get_page(url: str, session: requests.Session) -> Optional[BeautifulSoup]:
    """
    抓取单个页面，返回 BeautifulSoup 对象。
    失败时记录日志并返回 None。
    """
    ua = random.choice(config.KE_USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://sh.ke.com/",
    }
    try:
        resp = session.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as exc:
        logger.error("请求失败 %s：%s", url, exc)
        return None


# ─────────────────────────────────────────────
# 字段解析辅助函数
# ─────────────────────────────────────────────

def _safe_int(text: str, default: int = 0) -> int:
    """从字符串中提取第一个整数，失败返回 default。"""
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else default


def _safe_float(text: str, default: float = 0.0) -> float:
    """从字符串中提取第一个浮点数，失败返回 default。"""
    m = re.search(r"[\d.]+", text or "")
    return float(m.group()) if m else default


def _parse_listing(item, district: str, price_range: str) -> Optional[dict]:
    """
    解析单条房源 HTML 元素，返回字段字典。
    若关键字段缺失则返回 None。
    """
    try:
        # ── 小区名 ──
        community_tag = item.select_one(".positionInfo a.ml")
        community = community_tag.get_text(strip=True) if community_tag else ""

        # ── 板块/区域 ──
        position_tag = item.select_one(".positionInfo")
        position_text = position_tag.get_text(" ", strip=True) if position_tag else ""
        # 格式类似 "陆家嘴 / 浦东" 或 "浦东新区陆家嘴"
        block = position_text.split("/")[0].strip() if "/" in position_text else district

        # ── 标题（含房型信息）──
        title_tag = item.select_one(".title a")
        title = title_tag.get_text(strip=True) if title_tag else ""
        link = title_tag["href"] if title_tag and title_tag.has_attr("href") else ""
        if link and not link.startswith("http"):
            link = "https://sh.ke.com" + link

        # ── 房屋基本信息（面积、楼层、年份、房型）──
        house_info_tag = item.select_one(".houseInfo")
        house_info = house_info_tag.get_text(" ", strip=True) if house_info_tag else ""

        # 面积：匹配 "90.5平米" 格式
        area_match = re.search(r"([\d.]+)\s*平米", house_info)
        area = float(area_match.group(1)) if area_match else 0.0

        # 房型：匹配 "2室1厅" 格式
        room_match = re.search(r"(\d+室\d+厅)", house_info)
        room_type = room_match.group(1) if room_match else ""

        # 楼层：匹配 "中楼层/共26层" 格式
        floor_match = re.search(r"(低|中|高)楼层", house_info)
        floor_info = floor_match.group(0) if floor_match else ""

        # 建造年份：匹配 "2005年建" 格式
        year_match = re.search(r"(\d{4})年建", house_info)
        built_year = int(year_match.group(1)) if year_match else 0

        # ── 总价 ──
        total_price_tag = item.select_one(".totalPrice span")
        total_price_text = total_price_tag.get_text(strip=True) if total_price_tag else "0"
        total_price = _safe_float(total_price_text)  # 单位：万元

        # 如果总价不在筛选区间内，跳过（双重确认）
        low, high = (300, 500) if "300" in price_range else (500, 800)
        if total_price and not (low <= total_price <= high):
            return None

        # ── 单价 ──
        unit_price_tag = item.select_one(".unitPrice span")
        unit_price_text = unit_price_tag.get_text(strip=True) if unit_price_tag else "0"
        unit_price = _safe_int(unit_price_text)  # 单位：元/㎡

        # ── 挂牌时长 ──
        # 贝壳有时在 .followInfo 里显示 "23天以前发布"
        follow_tag = item.select_one(".followInfo")
        follow_text = follow_tag.get_text(" ", strip=True) if follow_tag else ""
        days_match = re.search(r"(\d+)\s*天以前", follow_text)
        listing_days = int(days_match.group(1)) if days_match else 0

        # ── 关注人数 ──
        attention_match = re.search(r"(\d+)\s*人关注", follow_text)
        attention_count = int(attention_match.group(1)) if attention_match else 0

        # ── 是否降价 ──
        tag_tags = item.select(".tag span, .tagList span")
        tag_texts = [t.get_text(strip=True) for t in tag_tags]
        is_price_drop = any("降价" in t or "价格下调" in t for t in tag_texts)

        if not community and not title:
            return None  # 无效条目

        return {
            "小区名":     community or title[:20],
            "所在区域":   district,
            "板块":       block,
            "总价(万元)": total_price,
            "单价(元/㎡)": unit_price,
            "面积(㎡)":   area,
            "房型":       room_type,
            "楼层":       floor_info,
            "建造年份":   built_year,
            "挂牌天数":   listing_days,
            "是否降价":   "是" if is_price_drop else "否",
            "关注人数":   attention_count,
            "价格区间":   price_range,
            "页面链接":   link,
            "采集时间":   datetime.now().strftime("%Y-%m-%d %H:%M"),
            "KOL提及次数": 0,  # 初始为0，由 kol_parser 更新
            "可信度评分": 0,   # 初始为0，由 validator 更新
        }
    except Exception as exc:
        logger.error("解析房源条目失败：%s", exc)
        return None


# ─────────────────────────────────────────────
# 单区域 + 单价格区间采集
# ─────────────────────────────────────────────

def scrape_district_price(
    district: str,
    price_range_label: str,
    session: requests.Session,
) -> list[dict]:
    """
    采集指定区域和价格区间的所有房源。
    返回房源字典列表。
    """
    district_path = DISTRICT_URL_MAP.get(district)
    price_param   = PRICE_RANGE_MAP.get(price_range_label)

    if not district_path or not price_param:
        logger.warning("未知区域或价格区间：%s / %s", district, price_range_label)
        return []

    all_listings: list[dict] = []

    for page in range(1, config.KE_MAX_PAGES + 1):
        # 贝壳分页格式：pg2 表示第2页
        page_seg = f"pg{page}" if page > 1 else ""
        url = f"{config.KE_BASE_URL}{district_path}/{price_param}/{page_seg}".rstrip("/") + "/"

        # robots.txt 检查（仅第一页检查一次）
        if page == 1 and not _check_robots_allowed(url):
            logger.warning("robots.txt 不允许采集 %s，跳过", url)
            break

        print(f"  正在采集第 {page} 页：{url}")
        soup = _get_page(url, session)

        if soup is None:
            print(f"  ✗ 第 {page} 页请求失败，跳过")
            break

        # 贝壳房源列表容器
        items = soup.select("ul.sellListContent li.clear")
        if not items:
            # 也尝试另一种选择器（贝壳页面结构可能有变化）
            items = soup.select(".listContent li")

        if not items:
            print(f"  → 第 {page} 页无数据，采集结束")
            break

        page_listings = []
        for item in items:
            listing = _parse_listing(item, district, price_range_label)
            if listing:
                page_listings.append(listing)

        print(f"  ✓ 第 {page} 页解析到 {len(page_listings)} 套有效房源")
        all_listings.extend(page_listings)

        # 随机延迟，模拟人工浏览节奏
        delay = random.uniform(config.KE_DELAY_MIN, config.KE_DELAY_MAX)
        time.sleep(delay)

    return all_listings


# ─────────────────────────────────────────────
# 主入口：采集所有区域 + 两个价格档
# ─────────────────────────────────────────────

def run_scraper(districts: Optional[list[str]] = None) -> int:
    """
    采集所有目标区域的 300-500万 和 500-800万 房源，写入飞书 01_房源总库。
    districts: 指定采集的区域列表，默认使用 config.TARGET_DISTRICTS。
    返回总写入条数。
    """
    if districts is None:
        districts = config.TARGET_DISTRICTS

    price_ranges = [
        (f"{config.BUDGET_LOW[0]}-{config.BUDGET_LOW[1]}万",),
        (f"{config.BUDGET_HIGH[0]}-{config.BUDGET_HIGH[1]}万",),
    ]
    # 展平为字符串列表
    price_labels = [p[0] for p in price_ranges]

    session = requests.Session()
    total_written = 0

    for district in districts:
        for price_label in price_labels:
            print(f"\n{'='*50}")
            print(f"▶ 开始采集：{district} | {price_label}")
            print(f"{'='*50}")

            try:
                listings = scrape_district_price(district, price_label, session)
                if not listings:
                    print(f"  ⚠ {district} {price_label} 未采集到数据")
                    continue

                print(f"  → 共采集 {len(listings)} 条，正在写入飞书...")
                ids = feishu_sdk.add_records(
                    config.FEISHU_BASE_ID_HOUSE,
                    config.FEISHU_TABLE_ID_HOUSE,
                    listings,
                )
                total_written += len(ids)
                print(f"  ✓ 成功写入 {len(ids)} 条")

            except Exception as exc:
                logger.error("采集 %s %s 时出错：%s", district, price_label, exc)
                print(f"  ✗ 出错：{exc}（已记录到 error.log）")

    print(f"\n{'='*50}")
    print(f"✅ 采集完成！共写入 {total_written} 条房源到 01_房源总库")
    print(f"{'='*50}\n")
    return total_written
