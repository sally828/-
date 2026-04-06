# -*- coding: utf-8 -*-
import re, sys, time, requests
sys.path.insert(0, '.')
import config

API = "https://open.feishu.cn/open-apis"
TEXT, NUMBER, URL = 1, 2, 15

def get_token():
    r = requests.post(
        f"{API}/auth/v3/tenant_access_token/internal",
        json={"app_id": config.FEISHU_APP_ID, "app_secret": config.FEISHU_APP_SECRET},
        timeout=15
    )
    d = r.json()
    if d.get("code") != 0:
        print("X token fail:", d); sys.exit(1)
    print("ok token"); return d["tenant_access_token"]

def hdr(tk):
    return {"Authorization": f"Bearer {tk}", "Content-Type": "application/json"}

def create_bitable(tk, name):
    r = requests.post(f"{API}/bitable/v1/apps", headers=hdr(tk), json={"name": name}, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        print("X create", name, d); sys.exit(1)
    at = d["data"]["app"]["app_token"]
    print("+", name, at)
    time.sleep(1)
    r2 = requests.get(f"{API}/bitable/v1/apps/{at}/tables", headers=hdr(tk), timeout=15)
    tid = r2.json()["data"]["items"][0]["table_id"]
    print("  tid", tid)
    return at, tid

def rename_first(tk, at, tid, new_name):
    r = requests.get(f"{API}/bitable/v1/apps/{at}/tables/{tid}/fields", headers=hdr(tk), timeout=15)
    items = r.json().get("data", {}).get("items", [])
    if not items: return
    fid = items[0]["field_id"]
    fn  = items[0]["field_name"]
    if fn == new_name: return
    requests.put(
        f"{API}/bitable/v1/apps/{at}/tables/{tid}/fields/{fid}",
        headers=hdr(tk), json={"field_name": new_name, "type": TEXT}, timeout=15
    )
    print("  rename", fn, "->", new_name)

def add_fields(tk, at, tid, fields):
    r = requests.get(f"{API}/bitable/v1/apps/{at}/tables/{tid}/fields", headers=hdr(tk), timeout=15)
    ex = {f["field_name"] for f in r.json().get("data", {}).get("items", [])}
    for fn, ft in fields:
        if fn in ex: continue
        r2 = requests.post(
            f"{API}/bitable/v1/apps/{at}/tables/{tid}/fields",
            headers=hdr(tk), json={"field_name": fn, "type": ft}, timeout=15
        )
        code = r2.json().get("code")
        print("  +", fn, "ok" if code == 0 else r2.json().get("msg"))
        time.sleep(0.2)

# 各表字段定义
f1 = "\u5c0f\u533a\u540d"
f2 = "\u6240\u5728\u533a\u57df"
HOUSE_FIELDS = [
    (f2, TEXT),
    ("\u677f\u5757", TEXT),
    ("\u603b\u4ef7(\u4e07\u5143)", NUMBER),
    ("\u5355\u4ef7(\u5143/\u33a1)", NUMBER),
    ("\u9762\u79ef(\u33a1)", NUMBER),
    ("\u623f\u578b", TEXT),
    ("\u697c\u5c42", TEXT),
    ("\u5efa\u9020\u5e74\u4efd", NUMBER),
    ("\u6302\u724c\u5929\u6570", NUMBER),
    ("\u662f\u5426\u964d\u4ef7", TEXT),
    ("\u5173\u6ce8\u4eba\u6570", NUMBER),
    ("\u4ef7\u683c\u533a\u95f4", TEXT),
    ("\u9875\u9762\u94fe\u63a5", URL),
    ("\u91c7\u96c6\u65f6\u95f4", TEXT),
    ("KOL\u63d0\u53ca\u6b21\u6570", NUMBER),
    ("\u53ef\u4fe1\u5ea6\u8bc4\u5206", NUMBER),
    ("\u8f6f\u6587\u6807\u8bb0", NUMBER),
]
REGION_FIELDS = [
    ("\u6392\u540d", NUMBER),
    ("\u7efc\u5408\u8bc4\u5206", NUMBER),
    ("\u5747\u4ef7(\u4e07\u5143)", NUMBER),
    ("\u5747\u4ef7(\u5143/\u33a1)", NUMBER),
    ("\u6302\u724c\u5929\u6570\u4e2d\u4f4d\u6570", NUMBER),
    ("\u5173\u6ce8\u4eba\u6570\u5747\u503c", NUMBER),
    ("\u964d\u4ef7\u7387(%)", NUMBER),
    ("KOL\u63d0\u53ca\u5747\u503c", NUMBER),
    ("\u5747\u4ef7\u7ade\u4e89\u529b\u5206", NUMBER),
    ("\u6210\u4ea4\u901f\u5ea6\u5206", NUMBER),
    ("\u70ed\u5ea6\u5206", NUMBER),
    ("\u4ef7\u683c\u7a33\u5b9a\u5206", NUMBER),
    ("KOL\u70ed\u5ea6\u5206", NUMBER),
    ("\u623f\u6e90\u6570\u91cf", NUMBER),
]
KOL_FIELDS = [
    ("\u63d0\u53ca\u5c0f\u533a", TEXT),
    ("\u63a8\u8350\u7406\u7531", TEXT),
    ("\u98ce\u9669\u63d0\u793a", TEXT),
    ("\u662f\u5426\u8f6f\u6587", TEXT),
    ("\u5f15\u6d41\u4fe1\u53f7", TEXT),
    ("\u9002\u5408\u9884\u7b97", TEXT),
    ("\u6587\u7ae0\u6458\u8981", TEXT),
    ("\u63d0\u53ca\u5c0f\u533a\u6570", NUMBER),
]
SHORTLIST_FIELDS = [
    (f1, TEXT),
    (f2, TEXT),
    ("\u677f\u5757", TEXT),
    ("\u603b\u4ef7(\u4e07\u5143)", NUMBER),
    ("\u5355\u4ef7(\u5143/\u33a1)", NUMBER),
    ("\u9762\u79ef(\u33a1)", NUMBER),
    ("\u623f\u578b", TEXT),
    ("\u697c\u5c42", TEXT),
    ("\u5efa\u9020\u5e74\u4efd", NUMBER),
    ("\u6302\u724c\u5929\u6570", NUMBER),
    ("\u662f\u5426\u964d\u4ef7", TEXT),
    ("\u5173\u6ce8\u4eba\u6570", NUMBER),
    ("KOL\u63d0\u53ca\u6b21\u6570", NUMBER),
    ("\u53ef\u4fe1\u5ea6\u8bc4\u5206", NUMBER),
    ("\u8bc4\u5206\u8bf4\u660e", TEXT),
    ("\u9884\u7b97\u6863\u4f4d", TEXT),
    ("\u4f18\u5148\u7ea7", TEXT),
    ("\u9875\u9762\u94fe\u63a5", URL),
]

TABLES = [
    ("01_\u623f\u6e90\u603b\u5e93",  f1,                         "house",     HOUSE_FIELDS),
    ("02_\u533a\u57df\u8bc4\u5206",  "\u533a\u57df\u540d\u79f0", "region",    REGION_FIELDS),
    ("03_KOL\u89c2\u70b9\u5e93",     "\u6765\u6e90\u8d26\u53f7", "kol",       KOL_FIELDS),
    ("04_\u770b\u623f\u6e05\u5355",  f1,                         "shortlist", SHORTLIST_FIELDS),
]

def update_config(D):
    with open("config.py", "r", encoding="utf-8") as f:
        s = f.read()
    mapping = {
        "house":     ("FEISHU_BASE_ID_HOUSE",     "FEISHU_TABLE_ID_HOUSE"),
        "region":    ("FEISHU_BASE_ID_REGION",     "FEISHU_TABLE_ID_REGION"),
        "kol":       ("FEISHU_BASE_ID_KOL",        "FEISHU_TABLE_ID_KOL"),
        "shortlist": ("FEISHU_BASE_ID_SHORTLIST",  "FEISHU_TABLE_ID_SHORTLIST"),
    }
    for k, (bk, tk) in mapping.items():
        s = re.sub(rf'({bk}\s*=\s*)"[^"]*"', rf'\1"{D["base_"+k]}"', s)
        s = re.sub(rf'({tk}\s*=\s*)"[^"]*"', rf'\1"{D["table_"+k]}"', s)
    with open("config.py", "w", encoding="utf-8") as f:
        f.write(s)
    print("\nOK config.py updated")

def main():
    print("=" * 50)
    print("Feishu bitable init")
    print("=" * 50)
    tk = get_token()
    D = {}
    for name, first_field, key, fields in TABLES:
        print("\n>", name)
        at, tid = create_bitable(tk, name)
        D[f"base_{key}"] = at
        D[f"table_{key}"] = tid
        rename_first(tk, at, tid, first_field)
        add_fields(tk, at, tid, fields)
        time.sleep(0.5)
    update_config(D)
    print("\n" + "=" * 50)
    print("ALL DONE! IDs saved to config.py:")
    for k, v in D.items():
        print(f"  {k} = {v}")
    print("\nNext: python main.py")

if __name__ == "__main__":
    main()
