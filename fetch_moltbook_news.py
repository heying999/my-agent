import os
import re
import json
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from urllib.parse import urljoin
from openai import OpenAI
from zoneinfo import ZoneInfo

def get_beijing_time() -> str:
    tz = ZoneInfo("Asia/Shanghai")
    return datetime.now(tz).strftime("%Y年%m月%d日 %H:%M")

def get_ai_client():
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key: 
        print("⚠️ 环境变量 DASHSCOPE_API_KEY 未设置")
        return None
    return OpenAI(api_key=api_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", timeout=30.0)

def incremental_translate(new_items: List[Dict], existing_items: List[Dict], client: OpenAI) -> List[Dict]:
    if not client or not new_items: return new_items
    trans_map = {it["url"]: it["title_cn"] for it in existing_items if it.get("title_cn") and len(it["title_cn"]) > 1}
    to_translate = [it for it in new_items if it["url"] not in trans_map]

    if not to_translate: 
        print("✅ 无需翻译新条目。")
        for it in new_items: it["title_cn"] = trans_map.get(it["url"], "")
        return new_items

    print(f"🌐 正在翻译 {len(to_translate)} 条新题目...", flush=True)
    chunk_size = 10 
    for i in range(0, len(to_translate), chunk_size):
        chunk = to_translate[i : i + chunk_size]
        prompt = (
            "你是一个科技翻译专家。请将以下英文标题翻译成中文。\n"
            "规则：严格一行对应一个，保持顺序，严禁解释。\n\n"
            + "\n".join([f"[{idx}] {it['title']}" for idx, it in enumerate(chunk)])
        )
        try:
            completion = client.chat.completions.create(model="qwen-plus", messages=[{"role": "user", "content": prompt}], temperature=0.1)
            res = completion.choices[0].message.content.strip().splitlines()
            cleaned = [re.sub(r'^\[\d+\]\s*', '', l).strip() for l in res if l.strip()]
            for j, it in enumerate(chunk):
                it["title_cn"] = cleaned[j] if j < len(cleaned) else it["title"]
        except Exception as e:
            print(f"❌ 翻译失败: {e}")
            for it in chunk: it["title_cn"] = it["title"]
    return new_items

def summarize_with_ai(items: List[Dict], client: OpenAI) -> str:
    if not client or not items: return "暂无摘要"
    titles = [it.get("title_cn") or it["title"] for it in items[:25]]
    prompt = "用简体中文总结今日 AI/科技 10 大核心动向。10条、加粗关键词、严禁英文。\n\n" + "\n".join(titles)
    try:
        completion = client.chat.completions.create(model="qwen-plus", messages=[{"role": "user", "content": prompt}])
        return completion.choices[0].message.content.strip()
    except: return "（摘要生成失败）"

def scrape_all_channels(urls: List[str], limit: int) -> List[Dict]:
    from playwright.sync_api import sync_playwright
    all_results = []
    
    with sync_playwright() as p:
        print("🔥 启动工业级采集引擎...", flush=True)
        # 增加参数：禁用自动化特征，防止被网站拦截
        browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-blink-features=AutomationControlled'])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080}
        )
        
        for url in urls:
            cat = url.split('/')[-1].upper()
            print(f"📡 正在攻克频道: {cat}", flush=True)
            page = context.new_page()
            
            # 注入脚本隐藏 Playwright 特征
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            try:
                # 策略 1: 不要等 networkidle（容易卡死），等 domcontentloaded 后配合手动硬等待
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                
                # 强行等待 8 秒，确保所有异步数据（AJAX）渲染完毕
                time.sleep(8) 
                
                # 策略 2: 预览内容。如果只看到 "Checking your browser"，说明被防火墙挡了
                body_preview = page.inner_text('body')[:150].replace('\n', ' ')
                print(f"👀 页面快照预览: {body_preview}", flush=True)

                # 策略 3: 语义化抓取。直接找所有包含 '/post/' 的 A 标签
                # 这比找 div 类名要稳定 100 倍
                post_links = page.locator('a[href*="/post/"]')
                link_count = post_links.count()
                print(f"🔍 扫描到 {link_count} 个文章候选链接", flush=True)

                count = 0
                seen_urls = set()
                for i in range(link_count):
                    if count >= limit: break
                    link_el = post_links.nth(i)
                    
                    # 提取标题和链接
                    title = link_el.inner_text().strip()
                    href = link_el.get_attribute("href")
                    
                    # 过滤噪音：太短的标题或重复的链接
                    if not title or len(title) < 10 or href in seen_urls:
                        continue
                        
                    full_url = urljoin("https://www.moltbook.com", href)
                    seen_urls.add(href)
                    
                    # 尝试寻找点赞数（通常就在 A 标签附近）
                    all_results.append({
                        "title": title,
                        "url": full_url,
                        "category": cat,
                        "title_cn": "",
                        "hot_info": "🔥 热门内容"
                    })
                    count += 1
                
                print(f"✅ {cat} 抓取成功，捕获 {count} 条内容", flush=True)

            except Exception as e:
                print(f"❌ {cat} 频道抓取异常: {e}", flush=True)
            finally:
                page.close()
        browser.close()
    return all_results

def main():
    print(f"🎬 机器人启动 | 北京时间: {get_beijing_time()}", flush=True)
    data_path = Path("data.json")
    config_path = Path("config.json")

    if not config_path.exists():
        print("❌ 错误: 根目录缺少 config.json")
        return

    config = json.loads(config_path.read_text())
    # 1. 执行抓取
    all_new = scrape_all_channels(config.get("target_urls", []), config.get("item_limit", 15))
    
    if not all_new:
        print("⚠️ 本次未抓取到任何内容。请检查 GitHub 日志中的'页面快照预览'。")

    # 2. 读取旧数据
    existing_items = []
    if data_path.exists():
        try:
            old_data = json.loads(data_path.read_text(encoding="utf-8"))
            existing_items = old_data.get("items", [])
        except: pass

    # 3. 翻译 & 总结
    client = get_ai_client()
    all_new = incremental_translate(all_new, existing_items, client)
    summary = summarize_with_ai(all_new + existing_items, client)

    # 4. 合并去重
    combined = all_new + existing_items
    unique, seen = [], set()
    for it in combined:
        if it["url"] not in seen:
            unique.append(it)
            seen.add(it["url"])

    # 5. 写入
    output = {
        "beijing_time": get_beijing_time(),
        "ai_summary": summary,
        "items": unique[:500]
    }
    
    data_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"🎉 任务结束！当前库存: {len(unique[:500])} 条。", flush=True)

if __name__ == "__main__":
    main()
