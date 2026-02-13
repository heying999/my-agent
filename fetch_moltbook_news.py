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
    if not api_key: return None
    return OpenAI(api_key=api_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", timeout=30.0)

def incremental_translate(new_items: List[Dict], existing_items: List[Dict], client: OpenAI) -> List[Dict]:
    """
    增量翻译：修复了对位错位问题，采用逐条检查和更严格的 Prompt
    """
    if not client or not new_items: return new_items
    
    # 建立现有缓存，避免重复翻译
    trans_map = {it["url"]: it["title_cn"] for it in existing_items if it.get("title_cn") and len(it["title_cn"]) > 1}
    
    to_translate = []
    for it in new_items:
        if it["url"] in trans_map:
            it["title_cn"] = trans_map[it["url"]]
        else:
            # 过滤掉明显不是标题的杂质，防止干扰 AI
            if len(it["title"]) > 5 and not it["title"].startswith(('▲', '▼', 'Posted')):
                to_translate.append(it)
            else:
                it["title_cn"] = it["title"]

    if not to_translate: return new_items

    # 每次处理一小批，确保对位准确
    chunk_size = 10 
    for i in range(0, len(to_translate), chunk_size):
        chunk = to_translate[i : i + chunk_size]
        
        # 强制 AI 按照特定格式返回，方便正则拆分
        prompt = (
            "你是一个科技翻译专家。请将以下英文标题翻译成中文。\n"
            "规则：\n1. 严格一行对应一个，严禁输出任何多余的解释或前缀。\n"
            "2. 保持顺序与输入完全一致。\n\n"
            + "\n".join([f"[{idx}] {it['title']}" for idx, it in enumerate(chunk)])
        )

        try:
            completion = client.chat.completions.create(
                model="qwen-plus", 
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1 # 降低随机性，保证稳定性
            )
            raw_res = completion.choices[0].message.content.strip().splitlines()
            
            # 清洗 AI 返回的内容（去掉 [0] 这种标记）
            cleaned_res = [re.sub(r'^\[\d+\]\s*', '', line).strip() for line in raw_res if line.strip()]
            
            # 严格对位赋值
            for j, it in enumerate(chunk):
                if j < len(cleaned_res):
                    it["title_cn"] = cleaned_res[j]
                else:
                    it["title_cn"] = it["title"] # 没翻译到则保留原文
                    
        except Exception as e:
            print(f"❌ 翻译批次失败: {e}")
            
    return new_items

def scrape_all_channels(urls: List[str], limit: int) -> List[Dict]:
    from playwright.sync_api import sync_playwright
    all_results = []
    
    

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0...")
        
        for url in urls:
            cat = url.split('/')[-1].upper()
            print(f"🚀 正在抓取: {cat}...")
            try:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # 等待卡片加载
                page.wait_for_selector('div.flex.flex-col.gap-1', timeout=15000)
                
                cards = page.query_selector_all('div.flex.flex-col.gap-1')
                count = 0
                for card in cards:
                    # --- 精准提取修复点 ---
                    # 不再抓取 card.inner_text()，而是直接定位标题所在的 <a> 标签
                    title_link = card.query_selector('a[href*="/post/"]')
                    if not title_link: continue
                    
                    # 获取 A 标签内的纯文本，这通常就是干净的题目
                    clean_title = title_link.inner_text().strip()
                    href = title_link.get_attribute("href")
                    
                    # 辅助：获取热度信息用于展示，但不混入标题
                    raw_all = card.inner_text()
                    score_match = re.search(r'[▲\^]\s*(\d+)', raw_all)
                    comment_match = re.search(r'(\d+)\s*comments', raw_all.lower())
                    
                    score = score_match.group(1) if score_match else "0"
                    cmts = comment_match.group(1) if comment_match else "0"

                    if len(clean_title) < 5: continue

                    all_results.append({
                        "title": clean_title,
                        "url": urljoin("https://www.moltbook.com", href),
                        "category": cat,
                        "title_cn": "", # 初始留空
                        "hot_info": f"🔥{score} · 💬{cmts}"
                    })
                    count += 1
                    if count >= limit: break
                page.close()
            except Exception as e:
                print(f"❌ {cat} 抓取失败: {e}")
        browser.close()
    return all_results

# summarize_with_ai 和 main 保持逻辑，但确保调用更新后的函数
# ... (其余部分保持不变)
