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

# ── 写入 auto_download.py ──────────────────────────────────────────────────
Write-Host "写入 auto_download.py ..." -ForegroundColor Cyan
@'
#!/usr/bin/env python3
# =============================================================================
# auto_download.py — 上海二手房数据分析系统 · 自动采集入口
#
# 用法（可从任意目录运行，包括 C:\Users\Sally）：
#   python auto_download.py
#   python auto_download.py --districts 浦东,徐汇
#   python auto_download.py --rescore-only
# =============================================================================

import argparse
import logging
import os
import sys

# ── 路径修复：将项目目录加入 sys.path ────────────────────────────────────────
_PROJECT_DIR = r"C:\shanghai-house-hunter"
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

# ── 日志配置 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(_PROJECT_DIR, "error.log"), encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

import config  # noqa: E402


def _banner(text):
    width = 60
    print("\n" + "=" * width)
    print(f"  {text}")
    print("=" * width)


def _status(step, total, text):
    print(f"\n【Step {step}/{total}】{text}")


def _check_config():
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
        print(f"\n   config.py 路径：{os.path.join(_PROJECT_DIR, 'config.py')}")
        return False
    if not config.CLAUDE_API_KEY:
        print("  ℹ  提示：CLAUDE_API_KEY 未填写，KOL文章解析功能不可用")
    return True


def run_full(districts=None):
    _banner("上海二手房数据分析系统 · 自动采集")
    print(f"  项目目录：{_PROJECT_DIR}")
    if districts:
        print(f"  目标区域：{', '.join(districts)}")
    else:
        print(f"  目标区域：{', '.join(config.TARGET_DISTRICTS)}（默认）")

    if not _check_config():
        return 1

    import scraper_ke, scorer, validator

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

    _status(2, 3, "区域评分计算")
    try:
        scorer.run_scorer()
        print("  ✓ 评分完成")
    except Exception as exc:
        logger.error("评分模块异常：%s", exc, exc_info=True)
        print(f"  ✗ 评分出错：{exc}（已记录，继续执行）")

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
    print(f"    └─ 500-800万：{stats['high']:>5} 套\n")
    return 0


def run_rescore_only():
    _banner("上海二手房数据分析系统 · 重算评分")
    if not _check_config():
        return 1

    import scorer, validator

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
    print(f"    └─ 500-800万：{stats['high']:>5} 套\n")
    return 0


def main():
    parser = argparse.ArgumentParser(description="上海二手房数据分析系统 · 自动采集脚本")
    parser.add_argument("--districts", metavar="区域1,区域2",
                        help="指定采集区域，逗号分隔（默认使用 config.py 的 TARGET_DISTRICTS）")
    parser.add_argument("--rescore-only", action="store_true",
                        help="跳过采集，仅重新计算评分和看房清单")
    args = parser.parse_args()

    if args.rescore_only:
        sys.exit(run_rescore_only())

    districts = None
    if args.districts:
        districts = [d.strip() for d in args.districts.split(",") if d.strip()]
    sys.exit(run_full(districts))


if __name__ == "__main__":
    main()
'@ | Set-Content -Encoding UTF8 auto_download.py

# ── 将 auto_download.py 复制到桌面的"搜索"文件夹 ──────────────────────────
$desktop   = [Environment]::GetFolderPath("Desktop")
$searchDir = Join-Path $desktop "搜索"
New-Item -ItemType Directory -Force -Path $searchDir | Out-Null
Write-Host "`n复制 auto_download.py 到 $searchDir ..." -ForegroundColor Cyan
Copy-Item -Path "$dir\auto_download.py" -Destination "$searchDir\auto_download.py" -Force
Write-Host "  ✓ 已复制到 $searchDir\auto_download.py" -ForegroundColor Green

# ── 安装 Python 依赖 ────────────────────────────────────────────────────────
Write-Host "`n安装 Python 依赖包 ..." -ForegroundColor Cyan
pip install requests beautifulsoup4 lxml anthropic

# ── 运行飞书初始化 ──────────────────────────────────────────────────────────
Write-Host "`n开始飞书建表初始化 ..." -ForegroundColor Green
python setup_feishu.py

# ── 完成提示 ────────────────────────────────────────────────────────────────
Write-Host "`n============================================" -ForegroundColor Green
Write-Host "  完成！在桌面搜索文件夹中运行：" -ForegroundColor Green
Write-Host "  cd `"$searchDir`"" -ForegroundColor Yellow
Write-Host "  python auto_download.py" -ForegroundColor Yellow
Write-Host "" -ForegroundColor Green
Write-Host "  或进入项目目录运行交互菜单：" -ForegroundColor Green
Write-Host "  cd C:\shanghai-house-hunter" -ForegroundColor Yellow
Write-Host "  python main.py" -ForegroundColor Yellow
Write-Host "============================================" -ForegroundColor Green
