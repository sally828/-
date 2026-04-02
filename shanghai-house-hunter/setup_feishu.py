#!/usr/bin/env python3
# =============================================================================
# setup_feishu.py — 飞书一键初始化脚本
# 在你的本地电脑运行一次，自动完成：
#   1. 获取飞书 token
#   2. 创建 4 张多维表格
#   3. 在每张表里建好所有字段
#   4. 自动把 Base ID / Table ID 写入 config.py
# =============================================================================

import re
import sys
import time
import requests

# ── 读取 config.py 里的凭证 ──────────────────────────────────────────────────
sys.path.insert(0, ".")
import config

API = "https://open.feishu.cn/open-apis"

# ── 字段类型常量 ──────────────────────────────────────────────────────────────
TEXT   = 1   # 文本 / 多行文本
NUMBER = 2   # 数字
URL    = 15  # 超链接


# ─────────────────────────────────────────────
# Token
# ─────────────────────────────────────────────

def get_token():
    resp = requests.post(
        f"{API}/auth/v3/tenant_access_token/internal",
        json={"app_id": config.FEISHU_APP_ID, "app_secret": config.FEISHU_APP_SECRET},
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        print(f"✗ 获取 token 失败：{data}")
        sys.exit(1)
    print("✓ 飞书 token 获取成功")
    return data["tenant_access_token"]


def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ─────────────────────────────────────────────
# 创建多维表格（Bitable App）
# ─────────────────────────────────────────────

def create_bitable(token, name):
    """
    创建一个新的多维表格，返回 (app_token, table_id)。
    app_token 即 Base ID，table_id 是默认第一张子表的 ID。
    """
    resp = requests.post(
        f"{API}/bitable/v1/apps",
        headers=headers(token),
        json={"name": name},
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        print(f"✗ 创建多维表格 [{name}] 失败：{data}")
        sys.exit(1)

    app_token = data["data"]["app"]["app_token"]
    print(f"  ✓ 创建成功：{name}  Base ID = {app_token}")

    # 获取默认表的 table_id
    time.sleep(1)  # 等待表格初始化
    resp2 = requests.get(
        f"{API}/bitable/v1/apps/{app_token}/tables",
        headers=headers(token),
        timeout=15,
    )
    data2 = resp2.json()
    if data2.get("code") != 0:
        print(f"✗ 获取表列表失败：{data2}")
        sys.exit(1)

    table_id = data2["data"]["items"][0]["table_id"]
    print(f"    Table ID = {table_id}")
    return app_token, table_id


# ─────────────────────────────────────────────
# 创建字段
# ─────────────────────────────────────────────

def add_fields(token, app_token, table_id, fields):
    """
    批量在指定表中创建字段。
    fields 格式：[("字段名", 字段类型), ...]
    飞书默认已有一个"标题"文本字段，跳过重名字段。
    """
    # 先查已有字段，避免重复创建
    resp = requests.get(
        f"{API}/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        headers=headers(token),
        timeout=15,
    )
    existing = {f["field_name"] for f in resp.json().get("data", {}).get("items", [])}

    for field_name, field_type in fields:
        if field_name in existing:
            continue  # 已存在，跳过
        body = {"field_name": field_name, "type": field_type}
        r = requests.post(
            f"{API}/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            headers=headers(token),
            json=body,
            timeout=15,
        )
        result = r.json()
        if result.get("code") != 0:
            print(f"    ⚠ 创建字段 [{field_name}] 失败：{result.get('msg')}")
        else:
            print(f"    + {field_name}")
        time.sleep(0.2)  # 避免触发限流


# ─────────────────────────────────────────────
# 各表字段定义
# ─────────────────────────────────────────────

# 01_房源总库字段（第一个"标题"字段飞书自动创建，对应"小区名"，后面追加其余字段）
FIELDS_HOUSE = [
    ("所在区域",   TEXT),
    ("板块",       TEXT),
    ("总价(万元)", NUMBER),
    ("单价(元/㎡)", NUMBER),
    ("面积(㎡)",   NUMBER),
    ("房型",       TEXT),
    ("楼层",       TEXT),
    ("建造年份",   NUMBER),
    ("挂牌天数",   NUMBER),
    ("是否降价",   TEXT),
    ("关注人数",   NUMBER),
    ("价格区间",   TEXT),
    ("页面链接",   URL),
    ("采集时间",   TEXT),
    ("KOL提及次数", NUMBER),
    ("可信度评分", NUMBER),
    ("软文标记",   NUMBER),
]

# 02_区域评分字段
FIELDS_REGION = [
    ("排名",           NUMBER),
    ("综合评分",       NUMBER),
    ("均价(万元)",     NUMBER),
    ("均价(元/㎡)",    NUMBER),
    ("挂牌天数中位数", NUMBER),
    ("关注人数均值",   NUMBER),
    ("降价率(%)",      NUMBER),
    ("KOL提及均值",    NUMBER),
    ("均价竞争力分",   NUMBER),
    ("成交速度分",     NUMBER),
    ("热度分",         NUMBER),
    ("价格稳定分",     NUMBER),
    ("KOL热度分",      NUMBER),
    ("房源数量",       NUMBER),
]

# 03_KOL观点库字段
FIELDS_KOL = [
    ("来源账号",   TEXT),
    ("提及小区",   TEXT),
    ("推荐理由",   TEXT),
    ("风险提示",   TEXT),
    ("是否软文",   TEXT),
    ("引流信号",   TEXT),
    ("适合预算",   TEXT),
    ("文章摘要",   TEXT),
    ("提及小区数", NUMBER),
]

# 04_看房清单字段
FIELDS_SHORTLIST = [
    ("所在区域",   TEXT),
    ("板块",       TEXT),
    ("总价(万元)", NUMBER),
    ("单价(元/㎡)", NUMBER),
    ("面积(㎡)",   NUMBER),
    ("房型",       TEXT),
    ("楼层",       TEXT),
    ("建造年份",   NUMBER),
    ("挂牌天数",   NUMBER),
    ("是否降价",   TEXT),
    ("关注人数",   NUMBER),
    ("KOL提及次数", NUMBER),
    ("可信度评分", NUMBER),
    ("评分说明",   TEXT),
    ("预算档位",   TEXT),
    ("优先级",     TEXT),
    ("页面链接",   URL),
]


# ─────────────────────────────────────────────
# 更新 config.py
# ─────────────────────────────────────────────

def update_config(ids: dict):
    """把生成的 Base ID / Table ID 写回 config.py。"""
    with open("config.py", "r", encoding="utf-8") as f:
        content = f.read()

    replacements = {
        'FEISHU_BASE_ID_HOUSE     = ""':     f'FEISHU_BASE_ID_HOUSE     = "{ids["base_house"]}"',
        'FEISHU_BASE_ID_REGION    = ""':     f'FEISHU_BASE_ID_REGION    = "{ids["base_region"]}"',
        'FEISHU_BASE_ID_KOL       = ""':     f'FEISHU_BASE_ID_KOL       = "{ids["base_kol"]}"',
        'FEISHU_BASE_ID_SHORTLIST = ""':     f'FEISHU_BASE_ID_SHORTLIST = "{ids["base_shortlist"]}"',
        'FEISHU_TABLE_ID_HOUSE     = ""':    f'FEISHU_TABLE_ID_HOUSE     = "{ids["table_house"]}"',
        'FEISHU_TABLE_ID_REGION    = ""':    f'FEISHU_TABLE_ID_REGION    = "{ids["table_region"]}"',
        'FEISHU_TABLE_ID_KOL       = ""':    f'FEISHU_TABLE_ID_KOL       = "{ids["table_kol"]}"',
        'FEISHU_TABLE_ID_SHORTLIST = ""':    f'FEISHU_TABLE_ID_SHORTLIST = "{ids["table_shortlist"]}"',
    }

    for old, new in replacements.items():
        content = content.replace(old, new)

    with open("config.py", "w", encoding="utf-8") as f:
        f.write(content)

    print("\n✓ config.py 已自动更新！")


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  飞书多维表格一键初始化")
    print("=" * 55)

    # 检查凭证
    if not config.FEISHU_APP_ID or not config.FEISHU_APP_SECRET:
        print("✗ 请先在 config.py 中填写 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        sys.exit(1)

    token = get_token()
    print()

    # 创建4张表
    tables = [
        ("01_房源总库",   FIELDS_HOUSE,     "house"),
        ("02_区域评分",   FIELDS_REGION,    "region"),
        ("03_KOL观点库",  FIELDS_KOL,       "kol"),
        ("04_看房清单",   FIELDS_SHORTLIST, "shortlist"),
    ]

    ids = {}
    for name, fields, key in tables:
        print(f"\n▶ 正在创建：{name}")
        app_token, table_id = create_bitable(token, name)
        ids[f"base_{key}"]  = app_token
        ids[f"table_{key}"] = table_id

        print(f"  正在创建字段...")
        # 飞书默认第一个字段是"标题"，我们先把它重命名为对应字段名
        _rename_first_field(token, app_token, table_id, name)
        add_fields(token, app_token, table_id, fields)
        time.sleep(0.5)

    # 写回 config.py
    update_config(ids)

    # 打印汇总
    print("\n" + "=" * 55)
    print("✅ 初始化完成！以下 ID 已自动写入 config.py：")
    print("=" * 55)
    for name, _, key in tables:
        print(f"  {name}")
        print(f"    Base ID  : {ids[f'base_{key}']}")
        print(f"    Table ID : {ids[f'table_{key}']}")
    print()
    print("下一步：运行  python main.py  启动主程序")


def _rename_first_field(token, app_token, table_id, table_name):
    """把默认的"标题"字段改名为对应表的主字段名。"""
    first_field_name = {
        "01_房源总库":   "小区名",
        "02_区域评分":   "区域名称",
        "03_KOL观点库":  "来源账号",
        "04_看房清单":   "小区名",
    }.get(table_name, "标题")

    # 查第一个字段的 ID
    resp = requests.get(
        f"{API}/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        headers=headers(token),
        timeout=15,
    )
    items = resp.json().get("data", {}).get("items", [])
    if not items:
        return
    first_id   = items[0]["field_id"]
    first_name = items[0]["field_name"]
    if first_name == first_field_name:
        return  # 已经是目标名，跳过

    requests.put(
        f"{API}/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{first_id}",
        headers=headers(token),
        json={"field_name": first_field_name, "type": TEXT},
        timeout=15,
    )
    print(f"    ✎ 默认字段「{first_name}」→「{first_field_name}」")


if __name__ == "__main__":
    main()
