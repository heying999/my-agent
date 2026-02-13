#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict # 确保导入
from urllib.parse import urljoin
from openai import OpenAI
from zoneinfo import ZoneInfo

def get_beijing_time() -> str:
    tz = ZoneInfo("Asia/Shanghai")
    return datetime.now(tz).strftime("%Y年%m月%d日 %H:%M")

def get_ai_client():
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key: return None
    return OpenAI(api_key=api_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", timeout=30.0)

def incremental_translate(new_items: List[Dict], existing_items: List[Dict], client: OpenAI) -> List[Dict]:
    """如果删除了 data.json，这里会全量翻译前 30 条"""
    if not client or not new_items: return new_items
    trans_map = {it["url"]: it["title_cn"] for it in existing_items if it.get("title_cn")}
    
    to_translate = []
    for it in new_items:
        if it["url"] in trans_map:
            it["title_cn"] = trans_map[it["url"]]
        else:
            to_translate.append(it)
    
    if not to_translate: return new_items

    # 刚重置时，文章很多，我们先翻译最前面的 30 条，剩下的以后慢慢翻
    max_batch = 30
    process_list = to_translate[:max_batch]
    print(f"🌐 正在翻译 {len(process_list)} 条新题目...", flush=True)
    
    chunk_size = 10
    for i in range(0, len(process_list), chunk_size):
        chunk = process_list[i : i + chunk_size]
        prompt = "将以下科技标题翻译成中文，只要输出翻译，一行一个：\n\n" + "\n".join([it["title"] for it in chunk])
        try:
            completion = client.chat.completions.create(model="qwen-plus", messages=[{"role": "user", "content": prompt}])
            res = completion.choices[0].message.content.strip().splitlines()
            for j, it in enumerate(chunk):
                if j < len(res):
                    it["title_cn"] = re.sub(r'^\d+[\.、\s]+', '', res[j].strip())
                else: it["title_cn"] = it["title"]
        except Exception as e: print(f"❌ 翻译失败: {e}", flush=True)
    return new_items

def summarize_with_ai(items: List[Dict], client: OpenAI) -> str:
    if not client or not items: return ""
    # 总结使用翻译后的标题
    titles = [it.get("title_cn", it["title"]) for it in items[:30]]
    prompt = "总结今日 10 大核心动向。要求：简体中文、10条、加粗关键词、严禁英文。\n\n" + "\n".join(f"- {t}" for t in titles)
    try:
        completion = client.chat.completions.create(model="qwen-plus", messages=[{"role": "user", "content": prompt}])
        return completion.choices[0].message.content.strip()
    except: return "- （总结生成失败）"

def scrape_all_channels(urls: List[str], limit: int) -> List[Dict]:
    from playwright.sync_api import sync_playwright
    all_results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        for url in urls:
            cat = url.split('/')[-1].upper()
            print(f"🚀 正在抓取: {cat}...", flush=True)
            try:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # 使用更通用的选择器：只要是包含 /post/ 的链接
                page.wait_for_selector('a[href*="/post/"]', timeout=20000)
                
                # 抓取所有文章链接
                links = page.query_selector_all('a[href*="/post/"]')
                count = 0
                seen_urls = set()
                
                for link in links:
                    href = link.get_attribute("href")
                    if not href or href in seen_urls: continue
                    
                    # 获取该链接所在的容器文字，用来提取热度
                    # 向上找两层通常能覆盖整个卡片
                    parent = link.evaluate_handle("el => el.parentElement.parentElement")
                    raw_text = parent.as_element().inner_text() if parent.as_element() else ""
                    
                    # 清洗题目：如果是那种包含赞数的文字，只取题目部分
                    title = link.inner_text().strip()
                    if not title or len(title) < 10 or "comments" in title.lower(): continue

                    # 提取赞数和评论数
                    score = re.search(r'[▲\^]\s*(\d+)', raw_text)
                    comments = re.search(r'(\d+)\s*comments', raw_text.lower())
                    score_val = score.group(1) if score else "0"
                    comment_val = comments.group(1) if comments else "0"

                    all_results.append({
                        "title": title.split('\n')[0], # 只要第一行
                        "url": urljoin("https://www.moltbook.com", href),
                        "category": cat,
                        "hot_info": f"🔥{score_val} · 💬{comment_val}"
                    })
                    seen_urls.add(href)
                    count += 1
                    if count >= limit: break
                
                print(f"✅ {cat} 抓取到 {count} 条。", flush=True)
                page.close()
            except Exception as e: print(f"❌ {cat} 抓取超时或错误: {e}", flush=True)
        browser.close()
    return all_results

def main():
    script_dir = Path(__file__).resolve().parent
    data_path = script_dir / "data.json"
    
    # 读取配置
    try:
        config = json.loads((script_dir / "config.json").read_text())
        urls = config.get("target_urls", [])
        limit = config.get("item_limit", 19)
    except:
        urls = ["https://www.moltbook.com/m/ai"]; limit = 19

    # 1. 抓取
    all_new = scrape_all_channels(urls, limit)
    if not all_new:
        print("⚠️ 未抓取到任何内容，请检查网址或选择器。", flush=True)

    # 2. 读取旧数据（如果已删除则为空）
    existing_items = []
    if data_path.exists():
        try: existing_items = json.loads(data_path.read_text(encoding="utf-8")).get("items", [])
        except: pass

    # 3. 翻译与总结
    client = get_ai_client()
    all_new = incremental_translate(all_new, existing_items, client)
    summary = summarize_with_ai(all_new, client)

    # 4. 去重合并
    combined = all_new + existing_items
    unique, seen = [], set()
    for it in combined:
        if it["url"] not in seen:
            unique.append(it); seen.add(it["url"])

    # 5. 保存回 data.json
    data_path.write_text(json.dumps({
        "beijing_time": get_beijing_time(),
        "ai_summary": summary,
        "items": unique[:500]
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"🎉 成功！当前 data.json 共有 {len(unique[:500])} 条情报。", flush=True)

if __name__ == "__main__":
    main()
