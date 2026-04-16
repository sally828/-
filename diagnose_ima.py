#!/usr/bin/env python3
"""
IMA 诊断脚本 — 只监听，不操作浏览器
用法：
  1. 先用 start_360.bat 启动360浏览器
  2. 手动在浏览器里打开 IMA，进入「追梦人的财经图书馆」
  3. 运行本脚本：python diagnose_ima.py
  4. 在浏览器里手动点击 2-3 个文件夹（包括子文件夹）
  5. 按 Enter，脚本保存 ima_diagnosis.json 发给我
"""
import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

CDP_URL   = "http://localhost:9222"
OUT_FILE  = Path(__file__).parent / "ima_diagnosis.json"

async def main():
    captured = []

    async with async_playwright() as pw:
        print("连接360浏览器...")
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            print(f"连接失败: {e}\n请先运行 start_360.bat")
            return

        ctx = browser.contexts[0] if browser.contexts else None
        if not ctx:
            print("没有找到浏览器上下文"); return

        # 拦截所有页面的所有响应
        async def on_resp(response):
            if "ima.qq.com" not in response.url:
                return
            try:
                ct = response.headers.get("content-type", "")
                body_raw = None
                if "json" in ct:
                    body_raw = await response.json()
                try:
                    req_body = json.loads(response.request.post_data or "{}")
                except Exception:
                    req_body = {}
                captured.append({
                    "url":        response.url,
                    "method":     response.request.method,
                    "status":     response.status,
                    "req_body":   req_body,
                    "req_headers": dict(response.request.headers),
                    "resp_ct":    ct,
                    "resp_body":  body_raw,
                })
            except Exception:
                pass

        for p in ctx.pages:
            p.on("response", on_resp)
        ctx.on("page", lambda np: np.on("response", on_resp))

        # 同时抓取当前页面的DOM结构
        pages_info = []
        for p in ctx.pages:
            try:
                url  = p.url
                # 取页面里所有可见文字节点（帮助了解DOM）
                texts = await p.evaluate("""() => {
                    const results = [];
                    const walker = document.createTreeWalker(
                        document.body, NodeFilter.SHOW_ELEMENT, null);
                    let node;
                    while (node = walker.nextNode()) {
                        const t = (node.innerText || node.textContent || '').trim();
                        if (t.length > 2 && t.length < 100 && !t.includes('\\n')) {
                            const tag   = node.tagName;
                            const cls   = node.className;
                            const title = node.getAttribute('title') || '';
                            const dataName = node.getAttribute('data-name') || '';
                            const ariaLabel = node.getAttribute('aria-label') || '';
                            results.push({tag, cls, text:t, title, dataName, ariaLabel});
                        }
                    }
                    return results.slice(0, 200);
                }""")
                pages_info.append({"url": url, "dom_sample": texts})
            except Exception as e:
                pages_info.append({"url": p.url, "error": str(e)})

        print(f"\n✅ 监听已启动，已发现 {len(ctx.pages)} 个标签页：")
        for p in ctx.pages:
            print(f"   {p.url}")

        print("\n" + "="*60)
        print("现在请在360浏览器里操作：")
        print("  1. 进入「追梦人的财经图书馆」")
        print("  2. 点击 2-3 个顶层文件夹")
        print("  3. 点击其中一个子文件夹")
        print("  （每次点击后等2秒）")
        print("="*60)
        print("\n操作完成后按 Enter 保存诊断文件…")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input, ">>> ")
        await asyncio.sleep(2)

        # 保存
        result = {
            "total_responses": len(captured),
            "pages": pages_info,
            "responses": captured,
        }
        OUT_FILE.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        print(f"\n✅ 诊断文件已保存：{OUT_FILE}")
        print(f"   共捕获 {len(captured)} 个响应")
        print(f"   请把这个文件发给 Claude 分析\n")

        # 打印摘要
        print("── API 响应摘要 ──")
        for r in captured:
            url   = r["url"].split("?")[0].split("/")[-1]
            code  = r.get("resp_body", {})
            code  = code.get("code", "?") if isinstance(code, dict) else "?"
            items = r.get("resp_body", {})
            items = len(items.get("knowledge_list", [])) if isinstance(items, dict) else 0
            print(f"  {r['method']} {url}  code={code}  items={items}")

asyncio.run(main())
