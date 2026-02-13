#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict  # 修正：补齐导入，解决 NameError
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
    """增量翻译逻辑：只翻译没有中文标题的新条目"""
    if not client: return new_items
    trans_map = {it["url"]: it["title_cn"] for it in existing_items if it.get("title_cn")}
    
    to_translate = []
    for it in new_items:
        if it["url"] in trans_map:
            it["title_cn"] = trans_map[it["url"]]
        else:
            to_translate.append(it)
    
    if not to_translate:
        return new_items

    max_batch = 30
    to_translate = to_translate[:max_batch]
    print(f"🌐 正在翻译 {len(to_translate)} 条新题目...", flush=True)
    
    chunk_size = 10
    for i in range(0, len(to_translate), chunk_size):
        chunk = to_translate[i : i + chunk_size]
        prompt = "将以下科技标题翻译成中文，只要输出翻译，一行一个：\n\n" + "\n".join([it["title"] for it in chunk])
        try:
            completion = client.chat.completions.create(model="qwen-plus", messages=[{"role": "user", "content": prompt}])
            res = completion.choices[0].message.content.strip().splitlines()
            for j, it in enumerate(chunk):
                if j < len(res):
                    it["title_cn"] = re.sub(r'^\d+[\.、\s]+', '', res[j].strip())
                else:
                    it["title_cn"] = it["title"]
        except Exception as e:
            print(f"❌ 翻译批次失败: {e}", flush=True)
    return new_items

def summarize_with_ai(items: List[Dict], client: OpenAI) -> str:
    if not client or not items: return ""
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
            print(f"🚀 正在抓取频道: {cat}...", flush=True)
            try:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_selector('a[href*="/post/"]', timeout=15000)
                
                # 抓取包含信息的卡片容器
                cards = page.query_selector_all('div.flex.flex-col.gap-1')
                if not cards: cards = page.query_selector_all('div.bg-slate-900') # 兼容模式
                
                count = 0
                for card in cards:
                    raw_text = card.inner_text().strip()
                    link_el = card.query_selector('a[href*="/post/"]')
                    if not link_el or not raw_text: continue
                    
                    href = link_el.get_attribute("href")
                    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
                    
                    # --- 精准提取题目 ---
                    # 寻找真正的题目行（排除作者、时间、赞数等废话）
                    clean_title = ""
                    for line in lines:
                        if any(x in line.lower() for x in ["posted by", "ago", "comments", "▲", "^"]): continue
                        clean_title = line
                        break
                    if not clean_title: clean_title = lines[0] # 兜底取第一行
                    
                    # --- 提取热度 (Score & Comments) ---
                    score = re.search(r'[▲\^]\s*(\d+)', raw_text)
                    comments = re.search(r'(\d+)\s*comments', raw_text.lower())
                    
                    score_val = score.group(1) if score else "0"
                    comment_val = comments.group(1) if comments else "0"
                    
                    all_results.append({
                        "title": clean_title[:200], # 限制长度防止 AI 出错
                        "url": urljoin("https://www.moltbook.com", href),
                        "category": cat,
                        "hot_info": f"🔥{score_val} · 💬{comment_val}" # 记录评论数
                    })
                    count += 1
                    if count >= limit: break
                page.close()
            except Exception as e: print(f"❌ {cat} 抓取超时: {e}", flush=True)
        browser.close()
    return all_results

def main():
    script_dir = Path(__file__).resolve().parent
    data_path = script_dir / "data.json"
    config = json.loads((script_dir / "config.json").read_text())
    
    all_new = scrape_all_channels(config.get("target_urls", []), config.get("item_limit", 19))
    
    existing_items = []
    if data_path.exists():
        try: existing_items = json.loads(data_path.read_text(encoding="utf-8")).get("items", [])
        except: pass

    client = get_ai_client()
    all_new = incremental_translate(all_new, existing_items, client)
    summary = summarize_with_ai(all_new, client)

    combined = all_new + existing_items
    unique, seen = [], set()
    for it in combined:
        if it["url"] not in seen:
            unique.append(it); seen.add(it["url"])

    data_path.write_text(json.dumps({
        "beijing_time": get_beijing_time(),
        "ai_summary": summary,
        "items": unique[:500]
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"🎉 任务结束，当前库存 {len(unique[:500])} 条。", flush=True)

if __name__ == "__main__":
    main()
