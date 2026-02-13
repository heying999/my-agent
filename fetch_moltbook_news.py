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
    """增量翻译：只翻译库里没有的新标题"""
    if not client or not new_items: return new_items

    # 1. 建立旧翻译索引 {url: title_cn}
    trans_map = {it["url"]: it["title_cn"] for it in existing_items if "title_cn" in it}
    
    # 2. 识别需要新翻译的条目
    to_translate = []
    for it in new_items:
        if it["url"] in trans_map:
            it["title_cn"] = trans_map[it["url"]]
        else:
            to_translate.append(it)
    
    if not to_translate:
        print("☕ 所有文章均已翻译过，跳过 API 调用。")
        return new_items

    # 3. 批量翻译新条目
    print(f"🌐 正在翻译 {len(to_translate)} 条新发现的情报...")
    prompt = "你是一个科技翻译。请将以下英文标题翻译成中文。要求准确专业，每行对应一个翻译，不要输出序号和多余文字：\n\n" + \
             "\n".join([it["title"] for it in to_translate])
    
    try:
        completion = client.chat.completions.create(
            model="qwen-plus",
            messages=[{"role": "user", "content": prompt}]
        )
        res = completion.choices[0].message.content.strip().splitlines()
        for i, it in enumerate(to_translate):
            if i < len(res):
                it["title_cn"] = re.sub(r'^\d+[\.、\s]+', '', res[i].strip())
            else:
                it["title_cn"] = it["title"]
    except Exception as e:
        print(f"❌ 翻译失败: {e}")
        for it in to_translate: it["title_cn"] = it["title"]
    
    return new_items

def summarize_with_ai(items: List[Dict], client: OpenAI) -> str:
    """生成 10 大核心动向总结"""
    if not client or not items: return ""
    
    # 优先使用中文标题进行总结，更准确
    titles = [it.get("title_cn", it["title"]) for it in items[:40]]
    prompt = (
        "你是一个科技新闻专家。请基于以下标题，用【简体中文】总结“今日 10 大核心动向”。\n"
        "要求：严格 10 条；Markdown 列表；每条 1 句简析；加粗核心关键词；【严禁输出英文】。\n\n"
        "标题列表：\n" + "\n".join(f"- {t}" for t in titles)
    )

    try:
        completion = client.chat.completions.create(
            model="qwen-plus",
            messages=[{"role": "user", "content": prompt}]
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ 总结失败: {e}")
        return "- （总结生成失败，请检查 API 状态）"

def scrape_channel(url: str, limit: int) -> List[Dict]:
    results = []
    cat = url.split('/')[-1].upper()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector('a[href*="/post/"]', timeout=20000)
            links = page.query_selector_all('a[href*="/post/"]')
            for link in links:
                href = link.get_attribute("href")
                text = link.inner_text().strip()
                if not href or "/post/" not in href or len(text) < 2: continue
                results.append({
                    "title": text,
                    "url": urljoin("https://www.moltbook.com", href),
                    "category": cat
                })
                if len(results) >= limit: break
        except Exception as e: print(f"抓取 {cat} 失败: {e}")
        finally: browser.close()
    return results

def main():
    script_dir = Path(__file__).resolve().parent
    config = json.loads((script_dir / "config.json").read_text())
    urls = config.get("target_urls", [])
    limit = config.get("item_limit", 20)
    
    # 加载旧数据
    data_path = script_dir / "data.json"
    existing_data = {}
    if data_path.exists():
        try: existing_data = json.loads(data_path.read_text(encoding="utf-8"))
        except: pass
    existing_items = existing_data.get("items", [])

    # 抓取新内容
    all_new = []
    for url in urls:
        all_new.extend(scrape_channel(url, limit))
        time.sleep(1)

    # 增量处理
    client = get_ai_client()
    all_new = incremental_translate(all_new, existing_items, client)
    summary = summarize_with_ai(all_new, client)

    # 去重并保存
    combined = all_new + existing_items
    unique = []
    seen = set()
    for it in combined:
        if it["url"] not in seen:
            unique.append(it)
            seen.add(it["url"])

    data_path.write_text(json.dumps({
        "beijing_time": get_beijing_time(),
        "ai_summary": summary,
        "items": unique[:500]
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 任务完成，当前库存 {len(unique[:500])} 条")

if __name__ == "__main__":
    main()
