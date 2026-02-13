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
from openai import OpenAI  # 需要 pip install openai
from zoneinfo import ZoneInfo

def get_beijing_time() -> str:
    tz = ZoneInfo("Asia/Shanghai")
    return datetime.now(tz).strftime("%Y年%m月%d日 %H:%M")

NAV_TEXT_BLACKLIST = {"login", "dashboard", "search", "loading", "moltbook", "beta", "mascot", "help", "developers", "privacy", "terms"}

def summarize_with_ai(titles: List[str]) -> str:
    """使用阿里云通义千问生成 10 大核心动向"""
    if not titles: return ""
    
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        print("未检测到 DASHSCOPE_API_KEY，使用本地简易模式")
        return "\n".join([f"- **关注**：{t}" for t in titles[:10]])

    client = OpenAI(
        api_key=api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    prompt = (
        "你是科技新闻专家。请基于以下标题汇总，用中文总结“今日 10 大核心动向”。\n"
        "要求：严格输出 10 条；使用 Markdown 列表（- 开头）；每条 1 句简析；加粗核心关键词。\n\n"
        "标题列表：\n" + "\n".join(f"- {t}" for t in titles[:40])
    )

    try:
        completion = client.chat.completions.create(
            model="qwen-plus",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = completion.choices[0].message.content.strip()
        # 确保只保留带列表符号的行
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip().startswith("-")]
        return "\n".join(lines[:10])
    except Exception as e:
        print(f"阿里云 API 调用失败: {e}")
        return "\n".join([f"- **关注**：{t}" for t in titles[:10]])

def scrape_channel(url: str, item_limit: int) -> List[Dict]:
    """抓取单频道并提取分类"""
    results = []
    category = url.split('/')[-1].upper() # 提取 URL 末尾并转大写作为分类标签
    
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
                if any(nav in text.lower() for nav in NAV_TEXT_BLACKLIST): continue
                
                results.append({
                    "title": text,
                    "url": urljoin("https://www.moltbook.com", href),
                    "category": category # 记录来源分类
                })
                if len(results) >= item_limit: break
        except Exception as e:
            print(f"抓取频道 {category} 失败: {e}")
        finally:
            browser.close()
    return results

def main():
    script_dir = Path(__file__).resolve().parent
    config_path = script_dir / "config.json"
    
    # 鲁棒性读取配置
    try:
        config = json.loads(config_path.read_text())
        urls = config.get("target_urls", [config.get("target_url")])
        limit = config.get("item_limit", 20)
    except:
        urls = ["https://www.moltbook.com/m/ai"]
        limit = 20
    
    all_new_items = []
    for url in urls:
        if not url: continue
        print(f"正在抓取频道: {url}")
        all_new_items.extend(scrape_channel(url, limit))
        time.sleep(1)

    # 生成 10 大总结
    summary = summarize_with_ai([it["title"] for it in all_new_items])
    
    # 读取旧数据并去重
    data_path = script_dir / "data.json"
    existing_items = []
    if data_path.exists():
        try:
            existing_items = json.loads(data_path.read_text(encoding="utf-8")).get("items", [])
        except: pass

    combined = all_new_items + existing_items
    unique_items = []
    seen_urls = set()
    for it in combined:
        if it["url"] not in seen_urls:
            unique_items.append(it)
            seen_urls.add(it["url"])

    # 保存结果
    data_path.write_text(json.dumps({
        "beijing_time": get_beijing_time(),
        "ai_summary": summary,
        "items": unique_items[:500]
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print(f"任务完成！当前储存情报: {len(unique_items[:500])} 条")

if __name__ == "__main__":
    from playwright.sync_api import sync_playwright
    main()
