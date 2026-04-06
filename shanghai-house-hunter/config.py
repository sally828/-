# =============================================================================
# config.py — 全局配置文件
# 请将所有 "" 空白处填入你的真实凭证，再运行程序
# =============================================================================

# ── 飞书开放平台凭证 ──────────────────────────────────────────────────────────
FEISHU_APP_ID     = "cli_a944809b53ba1cb1"          # 飞书应用的 App ID，形如 cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET = "VAURQj1G30i1EJVsVryukcR7aoZxkEhS"          # 飞书应用的 App Secret

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

# ── 链家采集参数 ─────────────────────────────────────────────────────────────
KE_BASE_URL  = "https://sh.lianjia.com/ershoufang/"
KE_MAX_PAGES = 10               # 每个区域最多采集页数（每页约30条）

# 浏览器 Cookie（从 Chrome/Edge 开发者工具 Network 标签复制）
# 用于绕过链家反爬验证，有效期约数小时，过期后重新复制
LIANJIA_COOKIES = "lianjia_uuid=4488f12d-275e-4479-ac8f-114ca59d34d4; select_city=310000; Hm_lvt_46bf127ac9b856df503ec2dbf942b67e=1775477289; HMACCOUNT=7346C3087B73B4C8; _jzqa=1.2941436352335173000.1775477289.1775477289.1775477289.1; _jzqc=1; _jzqx=1.1775477289.1775477289.1.jzqsr=so%2Ecom|jzqct=/link.-; _jzqckmp=1; sajssdk_2015_cross_new_user=1; sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219d62b171b6131-07a40c717c0f4c-f4f7526-1622400-19d62b171b714d0%22%2C%22%24device_id%22%3A%2219d62b171b6131-07a40c717c0f4c-f4f7526-1622400-19d62b171b714d0%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E8%87%AA%E7%84%B6%E6%90%9C%E7%B4%A2%E6%B5%81%E9%87%8F%22%2C%22%24latest_referrer%22%3A%22https%3A%2F%2Fwww.so.com%2Flink%3Fm%3DwIQgPI5HN%252F2RhETU9UdCPJp6sbUuJn1uAsUT6EOMFxOiGg1dlFYXMzZjztWYE2mU%252FpY8cLZ6xbe6M4NsY0GCysaxzKxv3otmcU%252B1%252FieaKeeWAokJcsRwK8KVba8M%253D%22%2C%22%24latest_referrer_host%22%3A%22www.so.com%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC%22%7D%7D; lianjia_ssid=65656627-028e-4673-b2ea-de427655eb70; _jzqb=1.2.10.1775477289.1; Hm_lpvt_46bf127ac9b856df503ec2dbf942b67e=1775477581"
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
