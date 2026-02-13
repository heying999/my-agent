#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
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
    if not api_key: return None
    return OpenAI(api_key=api_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")

def incremental_translate(new_items: List[Dict], existing_items: List[Dict], client: OpenAI) -> List[Dict]:
    """增量分批翻译：降低单次调用压力，提高稳定性"""
    if not client or not new_items: return new_items

    trans_map = {it["url"]: it["title_cn"] for it in existing_items if "title_cn" in it}
    to_translate = []
    for it in new_items:
        if it["url"] in trans_map:
            it["title_cn"] = trans_map[it["url"]]
        else:
            to_translate.append(it)
    
    if not to_translate:
        print("☕ 没有新文章需要翻译。")
        return new_items

    print(f"🌐 发现 {len(to_translate)} 条新内容，开始分批翻译...")
    
    # 每 10 条为一组进行翻译，防止 AI 卡死
    chunk_size = 10
    for i in range(0, len(to_translate), chunk_size):
        chunk = to_translate[i : i + chunk_size]
        print(f"正在翻译第 {i+1} 到 {i+len(chunk)} 条...")
        
        prompt = "你是一个专业的科技翻译。请将以下英文标题翻译成中文。只要输出翻译，一行一个：\n\n" + \
                 "\n".join([it["title"] for it in chunk])
        
        try:
            completion = client.chat.completions.create(
                model="qwen-plus",
                messages=[{"role": "user", "content": prompt}],
                timeout=30 # 设置单次请求超时
            )
            res = completion.choices[0].message.content.strip().splitlines()
            for j, it in enumerate(chunk):
                if j < len(res):
                    it["title_cn"] = re.sub(r'^\d+[\.、\s]+', '', res[j].strip())
                else:
                    it["title_cn"] = it["title"]
        except Exception as e:
            print(f"❌ 该批次翻译失败: {e}")
            for it in chunk: it["title_cn"] = it.get("title_cn", it["title"])
        
        time.sleep(0.5) # 微小间距，防止频率过高

    return new_items

def summarize_with_ai(items: List[Dict], client: OpenAI) -> str:
    if not client or not items: return ""
    titles = [it.get("title_cn", it["title"]) for it in items[:40]]
    prompt = "基于以下标题，总结今日 10 大核心动向。要求：简体中文、10条、Markdown列表、加粗关键词、严禁英文。\n\n" + "\n".join(f"- {t}" for t in titles)

    try:
        completion = client.chat.completions.create(
            model="qwen-plus",
            messages=[{"role": "user", "content": prompt}]
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ 总结生成失败: {e}")
        return "- （总结生成失败，请检查 API）"

def scrape_all_channels(urls: List[str], limit: int) -> List[Dict]:
    """复用浏览器上下文，极速抓取多频道"""
    from playwright.sync_api import sync_playwright
    all_results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        for url in urls:
            cat = url.split('/')[-1].upper()
            print(f"🚀 正在抓取 {cat}...")
            try:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_selector('a[href*="/post/"]', timeout=15000)
                
                links = page.query_selector_all('a[href*="/post/"]')
                count = 0
                for link in links:
                    href = link.get_attribute("href")
                    text = link.inner_text().strip()
                    if not href or "/post/" not in href or len(text) < 5: continue
                    
                    all_results.append({
                        "title": text,
                        "url": urljoin("https://www.moltbook.com", href),
                        "category": cat
                    })
                    count += 1
                    if count >= limit: break
                page.close()
                print(f"✅ {cat} 抓取完成，获取 {count} 条。")
            except Exception as e:
                print(f"❌ {cat} 访问超时或出错，跳过。")
        
        browser.close()
    return all_results

def main():
    script_dir = Path(__file__).resolve().parent
    data_path = script_dir / "data.json"
    
    # 加载配置
    config = json.loads((script_dir / "config.json").read_text())
    urls = config.get("target_urls", [])
    limit = config.get("item_limit", 19)

    # 1. 抓取 (复用浏览器)
    all_new = scrape_all_channels(urls, limit)

    # 2. 读取旧数据
    existing_items = []
    if data_path.exists():
        try: existing_items = json.loads(data_path.read_text(encoding="utf-8")).get("items", [])
        except: pass

    # 3. 增量翻译 + 总结
    client = get_ai_client()
    all_new = incremental_translate(all_new, existing_items, client)
    summary = summarize_with_ai(all_new, client)

    # 4. 去重合并 (保留 500 条)
    combined = all_new + existing_items
    unique = []
    seen = set()
    for it in combined:
        if it["url"] not in seen:
            unique.append(it)
            seen.add(it["url"])

    # 5. 保存
    data_path.write_text(json.dumps({
        "beijing_time": get_beijing_time(),
        "ai_summary": summary,
        "items": unique[:500]
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"🎉 任务完美结束！")

if __name__ == "__main__":
    main()
