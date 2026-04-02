# =============================================================================
# feishu_sdk.py — 飞书多维表格 API 封装
# 封装了 token 获取、记录增删改查，所有请求自带重试与错误处理
# =============================================================================

import time
import logging
import requests
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)

# 飞书 API 根地址
_FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

# 全局 token 缓存（避免每次请求都重新获取）
_token_cache: dict[str, Any] = {
    "token": None,
    "expires_at": 0,
}


# ─────────────────────────────────────────────
# Token 管理
# ─────────────────────────────────────────────

def get_token() -> str:
    """
    获取飞书 tenant_access_token。
    token 有效期约 2 小时，缓存未过期时直接返回缓存值。
    """
    now = time.time()
    # 提前 5 分钟刷新，避免边界情况
    if _token_cache["token"] and now < _token_cache["expires_at"] - 300:
        return _token_cache["token"]

    url = f"{_FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": config.FEISHU_APP_ID,
        "app_secret": config.FEISHU_APP_SECRET,
    }
    resp = _post_with_retry(url, json=payload, use_token=False)
    token = resp["tenant_access_token"]
    expire = resp.get("expire", 7200)

    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expire
    logger.debug("飞书 token 已刷新，有效期 %s 秒", expire)
    return token


# ─────────────────────────────────────────────
# 多维表格 CRUD
# ─────────────────────────────────────────────

def add_records(base_id: str, table_id: str, records: list[dict]) -> list[str]:
    """
    批量写入记录。
    records 格式：[{"字段名": 值, ...}, ...]
    返回新建的 record_id 列表。
    飞书单次最多写入 500 条，此处自动分批。
    """
    if not records:
        return []

    created_ids: list[str] = []
    batch_size = 500
    total = len(records)

    for start in range(0, total, batch_size):
        batch = records[start: start + batch_size]
        print(f"  → 正在写入第 {start + 1}–{min(start + batch_size, total)} 条（共 {total} 条）...")
        url = f"{_FEISHU_API_BASE}/bitable/v1/apps/{base_id}/tables/{table_id}/records/batch_create"
        payload = {"records": [{"fields": r} for r in batch]}
        resp = _post_with_retry(url, json=payload)
        for item in resp.get("data", {}).get("records", []):
            created_ids.append(item["record_id"])

    return created_ids


def update_record(base_id: str, table_id: str, record_id: str, fields: dict) -> bool:
    """
    更新单条记录的字段。
    返回 True 表示成功。
    """
    url = (
        f"{_FEISHU_API_BASE}/bitable/v1/apps/{base_id}/tables/{table_id}"
        f"/records/{record_id}"
    )
    payload = {"fields": fields}
    _put_with_retry(url, json=payload)
    return True


def list_records(
    base_id: str,
    table_id: str,
    filter_expr: Optional[str] = None,
    page_size: int = 500,
) -> list[dict]:
    """
    读取表中所有记录，自动处理分页。
    filter_expr：飞书公式过滤条件，例如 'CurrentValue.[状态]="待验证"'
    返回 [{"record_id": "...", "fields": {...}}, ...]
    """
    url = f"{_FEISHU_API_BASE}/bitable/v1/apps/{base_id}/tables/{table_id}/records"
    all_records: list[dict] = []
    page_token: Optional[str] = None
    page_num = 1

    while True:
        params: dict[str, Any] = {"page_size": page_size}
        if filter_expr:
            params["filter"] = filter_expr
        if page_token:
            params["page_token"] = page_token

        print(f"  → 正在读取第 {page_num} 页记录...")
        resp = _get_with_retry(url, params=params)
        data = resp.get("data", {})
        items = data.get("items", [])
        all_records.extend(items)

        if data.get("has_more") and data.get("page_token"):
            page_token = data["page_token"]
            page_num += 1
        else:
            break

    print(f"  ✓ 共读取 {len(all_records)} 条记录")
    return all_records


def delete_record(base_id: str, table_id: str, record_id: str) -> bool:
    """删除单条记录。"""
    url = (
        f"{_FEISHU_API_BASE}/bitable/v1/apps/{base_id}/tables/{table_id}"
        f"/records/{record_id}"
    )
    _delete_with_retry(url)
    return True


# ─────────────────────────────────────────────
# 内部：带重试的 HTTP 请求
# ─────────────────────────────────────────────

def _headers(use_token: bool = True) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if use_token:
        h["Authorization"] = f"Bearer {get_token()}"
    return h


def _check_response(resp: requests.Response) -> dict:
    """
    检查 HTTP 状态码和飞书业务状态码，异常时抛出 RuntimeError。
    """
    resp.raise_for_status()
    data = resp.json()
    code = data.get("code", 0)
    if code != 0:
        msg = data.get("msg", "未知错误")
        raise RuntimeError(f"飞书 API 错误 code={code}: {msg}")
    return data


def _retry(func, *args, max_retries: int = 3, **kwargs) -> dict:
    """通用重试装饰器逻辑，指数退避。"""
    last_exc: Exception = RuntimeError("未知错误")
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except (requests.RequestException, RuntimeError) as exc:
            last_exc = exc
            wait = 2 ** attempt
            logger.warning("请求失败（第 %d/%d 次）：%s，%ds 后重试...", attempt, max_retries, exc, wait)
            time.sleep(wait)
    raise last_exc


def _post_with_retry(url: str, json: dict, use_token: bool = True) -> dict:
    def _do():
        resp = requests.post(url, json=json, headers=_headers(use_token), timeout=30)
        return _check_response(resp)
    return _retry(_do)


def _put_with_retry(url: str, json: dict) -> dict:
    def _do():
        resp = requests.put(url, json=json, headers=_headers(), timeout=30)
        return _check_response(resp)
    return _retry(_do)


def _get_with_retry(url: str, params: dict) -> dict:
    def _do():
        resp = requests.get(url, params=params, headers=_headers(), timeout=30)
        return _check_response(resp)
    return _retry(_do)


def _delete_with_retry(url: str) -> dict:
    def _do():
        resp = requests.delete(url, headers=_headers(), timeout=30)
        return _check_response(resp)
    return _retry(_do)
