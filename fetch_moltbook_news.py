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
    # 增加超时控制，防止无限等待
    return OpenAI(api_key=api_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", timeout=30.0)

def incremental_translate(new_items: List[Dict], existing_items: List[Dict], client: OpenAI) -> List[Dict]:
    """增量翻译：加入强力日志刷新和单次上限控制"""
    if not client: return new_items

    # 建立索引
    trans_map = {it["url"]: it["title_cn"] for it in existing_items if "title_cn" in it}
    
    to_translate = []
    for it in new_items:
        if it["url"] in trans_map:
            it["title_cn"] = trans_map[it["url"]]
        else:
            to_translate.append(it)
    
    if not to_translate:
        print("☕ 没有新内容需要翻译。", flush=True)
        return new_items

    # --- 核心优化：单次翻译上限 30 条，防止卡死 ---
    max_batch = 30
    if len(to_translate) > max_batch:
        print(f"⚠️ 待翻译量大 ({len(to_translate)}条)，本轮仅处理前 {max_batch} 条。", flush=True)
        to_translate = to_translate[:max_batch]

    print(f"🌐 开始翻译 {len(to_translate)} 条新内容...", flush=True)
    
    chunk_size = 10
    for i in range(0, len(to_translate), chunk_size):
        chunk = to_translate[i : i + chunk_size]
        # 强制刷新 print，让你在 Actions 实时看到进度
        print(f" >> 正在处理批次: {i+1} - {i+len(chunk)}...", flush=True)
        
        prompt = "将以下科技标题翻译成中文，只要输出翻译，一行一个：\n\n" + "\n".join([it["title"] for it in chunk])
        
        try:
            completion = client.chat.completions.create(
                model="qwen-plus",
                messages=[{"role": "user", "content": prompt}]
            )
            res = completion.choices[0].message.content.strip().splitlines()
            for j, it in enumerate(chunk):
                if j < len(res):
                    it["title_cn"] = re.sub(r'^\d+[\.、\s]+', '', res[j].strip())
                else:
                    it["title_cn"] = it["title"]
        except Exception as e:
            print(f"❌ 批次失败: {e}", flush=True)
            for it in chunk: it["title_cn"] = it.get("title_cn", it["title"])
        
        time.sleep(1) # 适当停顿

    return new_items

def summarize_with_ai(items: List[Dict], client: OpenAI) -> str:
    if not client or not items: return ""
    # 总结也只取最近的，防止 Prompt 过长卡死
    titles = [it.get("title_cn", it["title"]) for it in items[:30]]
    prompt = "总结今日 10 大核心动向。要求：简体中文、10条列表、加粗关键词、严禁英文。\n\n" + "\n".join(f"- {t} " for t in titles)

    try:
        completion = client.chat.completions.create(
            model="qwen-plus",
            messages=[{"role": "user", "content": prompt}]
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ 总结生成失败: {e}", flush=True)
        return "- （总结生成失败，请检查 API 状态）"

def scrape_all_channels(urls: List[str], limit: int) -> List[Dict]:
    from playwright.sync_api import sync_playwright
    all_results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        for url in urls:
            cat = url.split('/')[-1].upper()
            print(f"🚀 正在抓取 {cat}...", flush=True)
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
                        "title": text, "url": urljoin("https://www.moltbook.com", href), "category": cat
                    })
                    count += 1
                    if count >= limit: break
                page.close()
                print(f"✅ {cat} 获取 {count} 条。", flush=True)
            except Exception as e:
                print(f"❌ {cat} 超时跳过。", flush=True)
        browser.close()
    return all_results

def main():
    script_dir = Path(__file__).resolve().parent
    data_path = script_dir / "data.json"
    config = json.loads((script_dir / "config.json").read_text())
    
    # 1. 抓取
    all_new = scrape_all_channels(config.get("target_urls", []), config.get("item_limit", 19))

    # 2. 读取
    existing_items = []
    if data_path.exists():
        try: existing_items = json.loads(data_path.read_text(encoding="utf-8")).get("items", [])
        except: pass

    # 3. 翻译与总结 (带 Flush 日志)
    client = get_ai_client()
    all_new = incremental_translate(all_new, existing_items, client)
    summary = summarize_with_ai(all_new, client)

    # 4. 去重
    combined = all_new + existing_items
    unique, seen = [], set()
    for it in combined:
        if it["url"] not in seen:
            unique.append(it); seen.add(it["url"])

    # 5. 保存
    data_path.write_text(json.dumps({
        "beijing_time": get_beijing_time(),
        "ai_summary": summary,
        "items": unique[:500]
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"🎉 任务成功结束，当前库存 {len(unique[:500])} 条。", flush=True)

if __name__ == "__main__":
    main()
