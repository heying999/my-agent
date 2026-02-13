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
    
    # 建立缓存映射
    trans_map = {it["url"]: it["title_cn"] for it in existing_items if it.get("title_cn") and len(it["title_cn"]) > 1}
    
    to_translate = []
    for it in new_items:
        if it["url"] in trans_map:
            it["title_cn"] = trans_map[it["url"]]
        else:
            to_translate.append(it)

    if not to_translate: 
        print("✅ 所有条目均已有翻译，跳过 API 调用。")
        return new_items

    print(f"🌐 正在翻译 {len(to_translate)} 条新题目...", flush=True)
    chunk_size = 10 
    for i in range(0, len(to_translate), chunk_size):
        chunk = to_translate[i : i + chunk_size]
        prompt = (
            "你是一个科技翻译专家。请将以下英文标题翻译成中文。\n"
            "规则：严格一行对应一个，严禁任何解释。保持顺序一致。\n\n"
            + "\n".join([f"[{idx}] {it['title']}" for idx, it in enumerate(chunk)])
        )
        try:
            completion = client.chat.completions.create(
                model="qwen-plus", 
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            raw_res = completion.choices[0].message.content.strip().splitlines()
            cleaned_res = [re.sub(r'^\[\d+\]\s*', '', line).strip() for line in raw_res if line.strip()]
            
            for j, it in enumerate(chunk):
                it["title_cn"] = cleaned_res[j] if j < len(cleaned_res) else it["title"]
        except Exception as e:
            print(f"❌ 翻译批次失败: {e}")
            for it in chunk: it["title_cn"] = it["title"]
            
    return new_items

def summarize_with_ai(items: List[Dict], client: OpenAI) -> str:
    if not client or not items: return "暂无摘要"
    # 取前20条进行总结
    titles = [it.get("title_cn") or it["title"] for it in items[:20]]
    prompt = "总结今日 AI 与科技 10 大动向。简体中文、10条、加粗关键词、严禁英文。\n\n" + "\n".join(titles)
    try:
        completion = client.chat.completions.create(model="qwen-plus", messages=[{"role": "user", "content": prompt}])
        return completion.choices[0].message.content.strip()
    except:
        return "（总结生成失败）"

def scrape_all_channels(urls: List[str], limit: int) -> List[Dict]:
    from playwright.sync_api import sync_playwright
    all_results = []
    with sync_playwright() as p:
        print("🔥 启动浏览器...", flush=True)
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        for url in urls:
            cat = url.split('/')[-1].upper()
            print(f"🚀 正在抓取频道: {cat}...", flush=True)
            try:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_selector('div.flex.flex-col.gap-1', timeout=20000)
                
                cards = page.query_selector_all('div.flex.flex-col.gap-1')
                print(f"📊 {cat} 发现 {len(cards)} 个卡片", flush=True)
                
                count = 0
                for card in cards:
                    title_link = card.query_selector('a[href*="/post/"]')
                    if not title_link: continue
                    
                    clean_title = title_link.inner_text().strip()
                    href = title_link.get_attribute("href")
                    
                    raw_all = card.inner_text()
                    score_match = re.search(r'[▲\^]\s*(\d+)', raw_all)
                    score = score_match.group(1) if score_match else "0"

                    if len(clean_title) < 5: continue

                    all_results.append({
                        "title": clean_title,
                        "url": urljoin("https://www.moltbook.com", href),
                        "category": cat,
                        "title_cn": "",
                        "hot_info": f"🔥{score}"
                    })
                    count += 1
                    if count >= limit: break
                page.close()
            except Exception as e:
                print(f"❌ {cat} 失败: {e}", flush=True)
        browser.close()
    return all_results

def main():
    print(f"⏰ 任务开始时间: {get_beijing_time()}", flush=True)
    # 强制路径：确保在 GitHub Actions 根目录运行
    data_path = Path("data.json")
    config_path = Path("config.json")

    if not config_path.exists():
        print("❌ 错误: 找不到 config.json")
        return

    config = json.loads(config_path.read_text())
    all_new = scrape_all_channels(config.get("target_urls", []), config.get("item_limit", 15))
    print(f"✅ 抓取完毕，共 {len(all_new)} 条数据", flush=True)

    existing_data = {"items": []}
    if data_path.exists():
        try:
            existing_data = json.loads(data_path.read_text(encoding="utf-8"))
        except:
            print("⚠️ 现有 data.json 损坏，将重新创建")

    client = get_ai_client()
    all_new = incremental_translate(all_new, existing_data.get("items", []), client)
    summary = summarize_with_ai(all_new, client)

    # 合并去重
    combined = all_new + existing_data.get("items", [])
    unique, seen = [], set()
    for it in combined:
        if it["url"] not in seen:
            unique.append(it)
            seen.add(it["url"])

    # 写入文件
    output = {
        "beijing_time": get_beijing_time(),
        "ai_summary": summary,
        "items": unique[:500]
    }
    
    data_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"🎉 任务成功！文件已更新。当前库存: {len(unique[:500])} 条。", flush=True)

if __name__ == "__main__":
    main()
