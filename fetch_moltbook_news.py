import os, re, json, time
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from urllib.parse import urljoin
from openai import OpenAI
from zoneinfo import ZoneInfo

def get_beijing_time():
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y年%m月%d日 %H:%M")

def get_ai_client():
    api_key = os.getenv("DASHSCOPE_API_KEY")
    return OpenAI(api_key=api_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", timeout=60.0) if api_key else None

def clean_text(text: str) -> str:
    """深度清洗：剔除投票箭头、点赞数、发布者信息，只留标题"""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    # 过滤规则：跳过前几行（通常是箭头和数字），寻找第一个单词数较多的行
    for line in lines:
        if any(char in line for char in ['▲', '▼', 'Posted by']): continue
        if len(line) > 10: return line
    return lines[-1] if lines else "Untitled"

def scrape_all_channels(urls: List[str], limit: int) -> List[Dict]:
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as p:
        print("🔥 启动采集引擎...", flush=True)
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = browser.new_context(user_agent="Mozilla/5.0...", viewport={'width': 1280, 'height': 800})
        for url in urls:
            cat = url.split('/')[-1].upper()
            print(f"📡 访问 {cat}...", flush=True)
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(10) # 强制等待 AJAX
                # 精准寻找包含 /post/ 的链接
                elements = page.query_selector_all('a[href*="/post/"]')
                count, seen_urls = 0, set()
                for el in elements:
                    if count >= limit: break
                    raw_text = el.inner_text()
                    href = el.get_attribute("href")
                    title = clean_text(raw_text) # 关键：进入清洗逻辑
                    
                    if len(title) < 10 or href in seen_urls: continue
                    seen_urls.add(href)
                    results.append({
                        "title": title,
                        "url": urljoin("https://www.moltbook.com", href),
                        "category": cat,
                        "title_cn": "",
                        "hot_info": "🔥 热门"
                    })
                    count += 1
                print(f"✅ {cat} 捕获 {count} 条", flush=True)
            except Exception as e: print(f"❌ {cat} 失败: {e}", flush=True)
            finally: page.close()
        browser.close()
    return results

def incremental_translate(items, old_items, client):
    if not client or not items: return items
    trans_map = {it["url"]: it["title_cn"] for it in old_items if it.get("title_cn") and len(it["title_cn"]) > 3}
    to_do = [it for it in items if it["url"] not in trans_map]
    
    if to_do:
        print(f"🌐 翻译新内容 ({len(to_do)} 条)...", flush=True)
        for i in range(0, len(to_do), 5):
            chunk = to_do[i:i+5]
            prompt = "请翻译以下科技标题为中文，严格一行一个，严禁任何解释：\n\n" + "\n".join([it["title"] for it in chunk])
            try:
                res = client.chat.completions.create(model="qwen-plus", messages=[{"role": "user", "content": prompt}]).choices[0].message.content
                lines = res.strip().splitlines()
                for j, it in enumerate(chunk):
                    it["title_cn"] = lines[j].strip() if j < len(lines) else it["title"]
            except: pass
    # 回填缓存
    for it in items:
        if it["url"] in trans_map: it["title_cn"] = trans_map[it["url"]]
    return items

def main():
    data_path = Path("data.json")
    config = json.loads(Path("config.json").read_text())
    all_new = scrape_all_channels(config["target_urls"], config["item_limit"])
    
    old_data = json.loads(data_path.read_text(encoding="utf-8")) if data_path.exists() else {"items": []}
    client = get_ai_client()
    all_new = incremental_translate(all_new, old_data.get("items", []), client)
    
    # 合并去重并保留 500 条
    unique, seen = [], set()
    for it in (all_new + old_data.get("items", [])):
        if it["url"] not in seen:
            unique.append(it); seen.add(it["url"])
    
    # 总结生成
    summary_prompt = "请用简体中文总结今日 AI 10 大核心动向。要求：10条、加粗关键词、严禁英文。\n\n" + "\n".join([it.get("title_cn") or it["title"] for it in unique[:20]])
    try:
        summary = client.chat.completions.create(model="qwen-plus", messages=[{"role": "user", "content": summary_prompt}]).choices[0].message.content
    except: summary = "总结生成中..."

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump({"beijing_time": get_beijing_time(), "ai_summary": summary, "items": unique[:500]}, f, ensure_ascii=False, indent=2)
    print("🎉 数据更新成功！", flush=True)

if __name__ == "__main__":
    main()
