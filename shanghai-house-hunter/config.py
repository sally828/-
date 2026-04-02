# =============================================================================
# config.py — 全局配置文件
# 请将所有 "" 空白处填入你的真实凭证，再运行程序
# =============================================================================

# ── 飞书开放平台凭证 ──────────────────────────────────────────────────────────
FEISHU_APP_ID     = ""          # 飞书应用的 App ID，形如 cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET = ""          # 飞书应用的 App Secret

# ── 飞书多维表格 Base ID（每张表一个独立 Base）────────────────────────────────
FEISHU_BASE_ID_HOUSE     = ""   # 01_房源总库    的 Base ID，形如 RHrXbxxxxxxx
FEISHU_BASE_ID_REGION    = ""   # 02_区域评分    的 Base ID
FEISHU_BASE_ID_KOL       = ""   # 03_KOL观点库   的 Base ID
FEISHU_BASE_ID_SHORTLIST = ""   # 04_看房清单    的 Base ID

# ── 飞书多维表格 Table ID（每张 Base 里的默认第一张表）───────────────────────
# 打开多维表格 → 右键表格标签 → 复制链接，URL 中 ?table= 后的部分即 Table ID
FEISHU_TABLE_ID_HOUSE     = ""  # 01_房源总库 表 ID，形如 tblxxxxxxxxxxxxxxxx
FEISHU_TABLE_ID_REGION    = ""  # 02_区域评分 表 ID
FEISHU_TABLE_ID_KOL       = ""  # 03_KOL观点库 表 ID
FEISHU_TABLE_ID_SHORTLIST = ""  # 04_看房清单 表 ID

# ── Claude / Anthropic API ────────────────────────────────────────────────────
CLAUDE_API_KEY   = ""           # 你的 Anthropic API Key，形如 sk-ant-api03-...
CLAUDE_MODEL     = "claude-sonnet-4-6"  # 使用的模型，可改为 claude-opus-4-6

# ── 预算档位（单位：万元）────────────────────────────────────────────────────
BUDGET_LOW  = (300, 500)        # 300-500 万元
BUDGET_HIGH = (500, 800)        # 500-800 万元

# ── 目标行政区 ────────────────────────────────────────────────────────────────
TARGET_DISTRICTS = ["浦东", "徐汇", "普陀", "长宁", "闵行", "杨浦", "虹口"]

# ── 贝壳找房采集参数 ──────────────────────────────────────────────────────────
KE_BASE_URL  = "https://sh.ke.com/ershoufang/"
KE_MAX_PAGES = 10               # 每个区域最多采集页数（每页约30条）
KE_DELAY_MIN = 2                # 请求间最短延迟（秒）
KE_DELAY_MAX = 5                # 请求间最长延迟（秒）

# 模拟浏览器 User-Agent 轮换列表，避免被封
KE_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# ── 区域评分权重（加总应 = 1.0）──────────────────────────────────────────────
SCORE_WEIGHTS = {
    "avg_price_score":    0.25,  # 均价竞争力（越低于全市均价分越高）
    "speed_score":        0.25,  # 成交速度（挂牌天数中位数越短越好）
    "hotness_score":      0.20,  # 关注热度（关注人数均值）
    "price_drop_score":   0.15,  # 降价率（越低越好）
    "kol_mention_score":  0.15,  # KOL提及热度
}

# ── 验证器可信度评分阈值 ──────────────────────────────────────────────────────
SHORTLIST_MIN_SCORE = 60        # 综合分 ≥ 60 才进看房清单

# ── 日志文件路径 ──────────────────────────────────────────────────────────────
LOG_FILE = "error.log"
