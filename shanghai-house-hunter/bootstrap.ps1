# ==============================================================================
# bootstrap.ps1 — 上海二手房分析系统 · Windows 一键安装脚本
# 使用方法：在 PowerShell 里运行此脚本，全程自动完成
# ==============================================================================

$dir = "C:\shanghai-house-hunter"
Write-Host "`n正在创建项目目录 $dir ..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Set-Location $dir

# ── 写入 config.py ─────────────────────────────────────────────────────────
Write-Host "写入 config.py ..." -ForegroundColor Cyan
@'
FEISHU_APP_ID     = "cli_a944809b53ba1cb1"
FEISHU_APP_SECRET = "VAURQj1G30i1EJVsVryukcR7aoZxkEhS"
FEISHU_BASE_ID_HOUSE     = ""
FEISHU_BASE_ID_REGION    = ""
FEISHU_BASE_ID_KOL       = ""
FEISHU_BASE_ID_SHORTLIST = ""
FEISHU_TABLE_ID_HOUSE     = ""
FEISHU_TABLE_ID_REGION    = ""
FEISHU_TABLE_ID_KOL       = ""
FEISHU_TABLE_ID_SHORTLIST = ""
CLAUDE_API_KEY   = ""
CLAUDE_MODEL     = "claude-sonnet-4-6"
BUDGET_LOW       = (300, 500)
BUDGET_HIGH      = (500, 800)
TARGET_DISTRICTS = ["浦东", "徐汇", "普陀", "长宁", "闵行", "杨浦", "虹口"]
KE_BASE_URL      = "https://sh.ke.com/ershoufang/"
KE_MAX_PAGES     = 10
KE_DELAY_MIN     = 2
KE_DELAY_MAX     = 5
KE_USER_AGENTS   = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]
SCORE_WEIGHTS = {
    "avg_price_score":   0.25,
    "speed_score":       0.25,
    "hotness_score":     0.20,
    "price_drop_score":  0.15,
    "kol_mention_score": 0.15,
}
SHORTLIST_MIN_SCORE = 60
LOG_FILE = "error.log"
'@ | Set-Content -Encoding UTF8 config.py

# ── 写入 setup_feishu.py ───────────────────────────────────────────────────
Write-Host "写入 setup_feishu.py ..." -ForegroundColor Cyan
@'
import re, sys, time, requests
sys.path.insert(0, ".")
import config

API  = "https://open.feishu.cn/open-apis"
TEXT = 1
NUMBER = 2
URL  = 15

def get_token():
    resp = requests.post(f"{API}/auth/v3/tenant_access_token/internal",
        json={"app_id": config.FEISHU_APP_ID, "app_secret": config.FEISHU_APP_SECRET}, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        print(f"✗ token 失败：{data}"); sys.exit(1)
    print("✓ token 获取成功")
    return data["tenant_access_token"]

def h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def create_bitable(token, name):
    resp = requests.post(f"{API}/bitable/v1/apps", headers=h(token), json={"name": name}, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        print(f"✗ 创建 [{name}] 失败：{data}"); sys.exit(1)
    app_token = data["data"]["app"]["app_token"]
    print(f"  ✓ {name}  Base ID = {app_token}")
    time.sleep(1)
    resp2 = requests.get(f"{API}/bitable/v1/apps/{app_token}/tables", headers=h(token), timeout=15)
    table_id = resp2.json()["data"]["items"][0]["table_id"]
    print(f"    Table ID = {table_id}")
    return app_token, table_id

def add_fields(token, app_token, table_id, fields):
    resp = requests.get(f"{API}/bitable/v1/apps/{app_token}/tables/{table_id}/fields", headers=h(token), timeout=15)
    existing = {f["field_name"] for f in resp.json().get("data", {}).get("items", [])}
    for name, ftype in fields:
        if name in existing: continue
        r = requests.post(f"{API}/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            headers=h(token), json={"field_name": name, "type": ftype}, timeout=15)
        if r.json().get("code") != 0:
            print(f"    ⚠ {name}: {r.json().get('msg')}")
        else:
            print(f"    + {name}")
        time.sleep(0.2)

def rename_first(token, app_token, table_id, new_name):
    resp = requests.get(f"{API}/bitable/v1/apps/{app_token}/tables/{table_id}/fields", headers=h(token), timeout=15)
    items = resp.json().get("data", {}).get("items", [])
    if not items or items[0]["field_name"] == new_name: return
    fid = items[0]["field_id"]
    requests.put(f"{API}/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{fid}",
        headers=h(token), json={"field_name": new_name, "type": TEXT}, timeout=15)
    print(f"    ✎ 标题 → {new_name}")

TABLES = [
    ("01_房源总库", "小区名", "house", [
        ("所在区域",TEXT),("板块",TEXT),("总价(万元)",NUMBER),("单价(元/㎡)",NUMBER),
        ("面积(㎡)",NUMBER),("房型",TEXT),("楼层",TEXT),("建造年份",NUMBER),
        ("挂牌天数",NUMBER),("是否降价",TEXT),("关注人数",NUMBER),("价格区间",TEXT),
        ("页面链接",URL),("采集时间",TEXT),("KOL提及次数",NUMBER),("可信度评分",NUMBER),("软文标记",NUMBER),
    ]),
    ("02_区域评分", "区域名称", "region", [
        ("排名",NUMBER),("综合评分",NUMBER),("均价(万元)",NUMBER),("均价(元/㎡)",NUMBER),
        ("挂牌天数中位数",NUMBER),("关注人数均值",NUMBER),("降价率(%)",NUMBER),
        ("KOL提及均值",NUMBER),("均价竞争力分",NUMBER),("成交速度分",NUMBER),
        ("热度分",NUMBER),("价格稳定分",NUMBER),("KOL热度分",NUMBER),("房源数量",NUMBER),
    ]),
    ("03_KOL观点库", "来源账号", "kol", [
        ("提及小区",TEXT),("推荐理由",TEXT),("风险提示",TEXT),("是否软文",TEXT),
        ("引流信号",TEXT),("适合预算",TEXT),("文章摘要",TEXT),("提及小区数",NUMBER),
    ]),
    ("04_看房清单", "小区名", "shortlist", [
        ("所在区域",TEXT),("板块",TEXT),("总价(万元)",NUMBER),("单价(元/㎡)",NUMBER),
        ("面积(㎡)",NUMBER),("房型",TEXT),("楼层",TEXT),("建造年份",NUMBER),
        ("挂牌天数",NUMBER),("是否降价",TEXT),("关注人数",NUMBER),("KOL提及次数",NUMBER),
        ("可信度评分",NUMBER),("评分说明",TEXT),("预算档位",TEXT),("优先级",TEXT),("页面链接",URL),
    ]),
]

def update_config(ids):
    with open("config.py", "r", encoding="utf-8") as f: content = f.read()
    for key, val in ids.items():
        content = re.sub(rf'({key}\s*=\s*)"[^"]*"', rf'\1"{val}"', content)
    with open("config.py", "w", encoding="utf-8") as f: f.write(content)
    print("\n✓ config.py 已自动更新")

def main():
    print("="*50 + "\n  飞书多维表格一键初始化\n" + "="*50)
    token = get_token()
    ids = {}
    for name, first_field, key, fields in TABLES:
        print(f"\n▶ 创建：{name}")
        app_token, table_id = create_bitable(token, name)
        ids[f"FEISHU_BASE_ID_{key.upper()}"]  = app_token
        ids[f"FEISHU_TABLE_ID_{key.upper()}"] = table_id
        rename_first(token, app_token, table_id, first_field)
        add_fields(token, app_token, table_id, fields)
        time.sleep(0.5)
    update_config(ids)
    print("\n" + "="*50)
    print("✅ 全部完成！现在运行 python main.py 启动主程序")
    print("="*50)

if __name__ == "__main__":
    main()
'@ | Set-Content -Encoding UTF8 setup_feishu.py

# ── 安装 Python 依赖 ────────────────────────────────────────────────────────
Write-Host "`n安装 Python 依赖包 ..." -ForegroundColor Cyan
pip install requests beautifulsoup4 lxml anthropic

# ── 运行飞书初始化 ──────────────────────────────────────────────────────────
Write-Host "`n开始飞书建表初始化 ..." -ForegroundColor Green
python setup_feishu.py

# ── 完成提示 ────────────────────────────────────────────────────────────────
Write-Host "`n============================================" -ForegroundColor Green
Write-Host "  完成！运行主程序请执行：" -ForegroundColor Green
Write-Host "  cd C:\shanghai-house-hunter" -ForegroundColor Yellow
Write-Host "  python main.py" -ForegroundColor Yellow
Write-Host "============================================" -ForegroundColor Green
