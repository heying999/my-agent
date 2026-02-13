#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import time
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from urllib.parse import urljoin

from duckduckgo_search import DDGS
from playwright.sync_api import sync_playwright
from zoneinfo import ZoneInfo

def get_beijing_time() -> str:
    tz = ZoneInfo("Asia/Shanghai")
    return datetime.now(tz).strftime("%Y年%m月%d日 %H:%M")

NAV_TEXT_BLACKLIST = {"login", "dashboard", "search", "loading", "moltbook", "beta", "mascot", "help", "developers", "privacy", "terms"}

def summarize_with_ddg(titles: List[str]) -> str:
    if not titles: return ""
    
    # 1. 修改 Prompt，要求 10 条总结
    prompt = (
        "你是科技资讯编辑。请基于以下标题列表，用中文总结“今日 10 大核心动向”。\n"
        "要求：严格输出 10 条；使用 Markdown 无序列表（- 开头）；每条 1 句简述；不要有噪音内容。\n\n"
        "标题列表：\n" + "\n".join(f"- {t}" for t in titles[:40])
    )

    try:
        raw = DDGS().chat(prompt, model="gpt-4o-mini").strip()
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip().startswith("-")]
        return "\n".join(lines[:10]) # 确保返回 10 条
    except:
        return "- （AI 总结服务暂时不可用，请稍后刷新）"

def scrape_channel(url: str, item_limit: int) -> List[Dict]:
    results = []
    category = url.split('/')[-1] # 提取 URL 最后的部分作为分类名
    
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
                    "category": category # 关键：保存分类
                })
                if len(results) >= item_limit: break
        except Exception as e:
            print(f"抓取 {category} 出错: {e}")
        finally:
            browser.close()
    return results

def save_data_incremental(output_path: Path, beijing_time: str, ai_summary: str, new_items: List[Dict]) -> None:
    existing_items = []
    if output_path.exists():
        try:
            existing_items = json.loads(output_path.read_text(encoding="utf-8")).get("items", [])
        except: pass

    # 合并并去重
    combined = new_items + existing_items
    unique_items = []
    seen_urls = set()
    for it in combined:
        if it["url"] not in seen_urls:
            unique_items.append(it)
            seen_urls.add(it["url"])

    output_path.write_text(json.dumps({
        "beijing_time": beijing_time,
        "ai_summary": ai_summary,
        "items": unique_items[:500]
    }, ensure_ascii=False, indent=2), encoding="utf-8")

def main():
    script_dir = Path(__file__).resolve().parent
    config = json.loads((script_dir / "config.json").read_text())
    urls = config.get("target_urls", [])
    limit = config.get("item_limit", 20)
    
    all_new_items = []
    for url in urls:
        print(f"正在抓取: {url}")
        all_new_items.extend(scrape_channel(url, limit))
        time.sleep(1)

    summary = summarize_with_ddg([it["title"] for it in all_new_items])
    save_data_incremental(script_dir / "data.json", get_beijing_time(), summary, all_new_items)

if __name__ == "__main__":
    main()
