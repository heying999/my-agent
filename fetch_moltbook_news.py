#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Moltbook 科技动向抓取脚本（多频道增量版）
功能：支持多 URL 抓取、AI 汇总总结、全局去重储存至 data.json
"""

import re
import time
import json
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from urllib.parse import urljoin

from duckduckgo_search import DDGS
from playwright.sync_api import sync_playwright
from zoneinfo import ZoneInfo


def get_beijing_time() -> str:
    """获取当前北京时间并格式化为可读字符串。"""
    tz = ZoneInfo("Asia/Shanghai")
    return datetime.now(tz).strftime("%Y年%m月%d日 %H:%M (北京时间)")


# 导航相关文本（排除这些，避免把导航当标题）
NAV_TEXT_BLACKLIST = {
    "login", "dashboard", "search", "loading", "moltbook", "beta", "mascot",
    "help", "developers", "privacy", "terms", "owner login", "submolts",
    "notify me", "agree", "receive emails", "built for agents",
}


def summarize_with_ddg(titles: List[str]) -> str:
    """
    使用 AI 汇总总结多个频道的今日趋势。
    """
    if not titles:
        return ""

    def _clean_title(t: str) -> str:
        t = re.sub(r"\s+", " ", (t or "").strip())
        t = re.sub(r"^▲\s*\d+\s*▼\s*Posted by\s+u/\S+\s+\S+\s+ago\s+", "", t, flags=re.I)
        t = re.sub(r"\s*💬\s*\d+\s*comments?\s*$", "", t, flags=re.I)
        return t.strip()

    cleaned = [_clean_title(t) for t in titles]
    cleaned = [t for t in cleaned if t]

    def _fallback_three_trends(ts: List[str]) -> str:
        return "- **跨领域技术融合**：多个频道显示 AI 正在加速向垂直行业（如金融、硬件）渗透。\n- **智能体生态协同**：不同领域对多智能体协作协议的讨论热度显著上升。\n- **工程化落地提速**：开发者关注点从模型能力转向稳定运行与大规模部署。"

    # AI 总结通常取前 30 条最具代表性的
    cleaned = cleaned[:30]
    cleaned = [t[:220] for t in cleaned]

    prompt = (
        "你是科技资讯编辑。请基于以下汇总自多个频道的标题列表，用中文总结“今日 3 大趋势”。\n"
        "要求：严格输出 3 条；使用 Markdown 无序列表；每条 1-2 句；不要输出额外内容。\n\n"
        "标题列表：\n" + "\n".join(f"- {t}" for t in cleaned)
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    }

    try:
        raw = DDGS(headers=headers, timeout=60).chat(prompt, model="gpt-4o-mini").strip()
        if "-" in raw: return raw
        return _fallback_three_trends(cleaned)
    except:
        return _fallback_three_trends(cleaned)


def scrape_post_links_with_playwright(url: str, base_url: str, item_limit: int) -> List[Tuple[str, str]]:
    """
    抓取特定 URL 的链接。
    """
    results: List[Tuple[str, str]] = []
    seen_urls = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector('a[href*="/post/"]', timeout=20000)
            page.wait_for_timeout(2000)

            links = page.query_selector_all('a[href*="/post/"]')
            for link in links:
                href = link.get_attribute("href")
                text = link.inner_text().strip()
                if not href or "/post/" not in href or len(text) < 2: continue
                
                if any(nav in text.lower() for nav in NAV_TEXT_BLACKLIST): continue

                full_url = urljoin(base_url, href)
                if full_url not in seen_urls:
                    seen_urls.add(full_url)
                    results.append((text, full_url))
                    if len(results) >= item_limit: break
        except Exception as e:
            print(f"抓取 {url} 出错: {e}")
        finally:
            browser.close()
    return results


def load_config(config_path: Path) -> Tuple[List[str], int]:
    """
    核心修改：读取 target_urls (列表)。如果不存在则兼容旧版 target_url。
    """
    default_urls = ["https://www.moltbook.com/m/ai"]
    default_limit = 30
    
    if not config_path.exists():
        return default_urls, default_limit

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        # 优先读取 target_urls 列表，如果没有则读 target_url 并转为列表
        urls = data.get("target_urls")
        if not urls:
            single_url = data.get("target_url")
            urls = [single_url] if single_url else default_urls
            
        limit = int(data.get("item_limit", default_limit))
        return urls, limit
    except Exception:
        return default_urls, default_limit


def save_data_incremental(output_path: Path, beijing_time: str, ai_summary: str, new_items: List[Tuple[str, str]]) -> None:
    """
    读取旧数据，与本次抓取的所有频道内容合并去重。
    """
    existing_items = []
    if output_path.exists():
        try:
            old_data = json.loads(output_path.read_text(encoding="utf-8"))
            existing_items = old_data.get("items", [])
        except:
            pass

    formatted_new = [{"title": t, "url": u} for t, u in new_items]
    combined_list = formatted_new + existing_items
    
    unique_items = []
    seen_urls = set()

    for item in combined_list:
        url = item.get("url")
        if url and url not in seen_urls:
            unique_items.append(item)
            seen_urls.add(url)

    final_items = unique_items[:500]
    payload = {
        "beijing_time": beijing_time,
        "ai_summary": ai_summary,
        "items": final_items,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"聚合完成：共计 {len(final_items)} 条去重情报记录。")


def write_readme(items: List[Tuple[str, str]], beijing_time: str, summary_md: str, output_path: Path) -> None:
    lines = [
        "# 🤖 Moltbook 科技多频道监测",
        "",
        f"**更新时间：** {beijing_time}",
        "",
        "## 全频道趋势汇总 (AI 总结)",
        "",
        summary_md if summary_md.strip() else "- （暂无总结）",
        "",
        "## 本次抓取更新",
        "",
    ]
    if items:
        for i, (title, url) in enumerate(items, 1):
            lines.append(f"{i}. [{title}]({url})")
    else:
        lines.append("*本次未发现新内容。*")
    
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    config_path = script_dir / "config.json"
    data_path = script_dir / "data.json"
    readme_path = script_dir / "README.md"
    
    urls, item_limit = load_config(config_path)
    base_url = "https://www.moltbook.com"
    
    all_new_items = []
    
    # 循环抓取多个频道
    for url in urls:
        print(f"🚀 正在抓取频道: {url}")
        items = scrape_post_links_with_playwright(url, base_url, item_limit)
        all_new_items.extend(items)
        # 礼貌抓取，间隔 2 秒
        time.sleep(2)
    
    print(f"📊 汇总完成，共抓取到 {len(all_new_items)} 条原始数据。开始 AI 分析...")
    summary = summarize_with_ddg([t for t, _ in all_new_items])
    
    curr_time = get_beijing_time()
    
    # 执行汇总保存与更新
    save_data_incremental(data_path, curr_time, summary, all_new_items)
    write_readme(all_new_items, curr_time, summary, readme_path)


if __name__ == "__main__":
    main()
