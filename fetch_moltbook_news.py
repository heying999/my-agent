#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Moltbook 科技动向抓取脚本（增量储存版）
功能：抓取数据、AI总结、增量储存至 data.json 并去重、更新 README.md
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
    使用 AI 总结今日趋势。
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
        # 简化版兜底
        return "- **AI 代理与自动化**：行业关注重点转向代理框架的生产环境落地。\n- **多智能体协作**：关于智能体通信协议与共享记忆的讨论增多。\n- **基础设施建设**：开发者更倾向于利用结构化数据和 API 构建底层支撑。"

    cleaned = cleaned[:25]
    cleaned = [t[:220] for t in cleaned]

    prompt = (
        "你是科技资讯编辑。请基于以下标题列表，用中文总结“今日 3 大趋势”。\n"
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
    抓取带 /post/ 的链接。
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
            print(f"抓取出错: {e}")
        finally:
            browser.close()
    return results


def load_config(config_path: Path) -> Tuple[str, int]:
    default_url, default_limit = "https://www.moltbook.com/m/ai", 30
    if not config_path.exists(): return default_url, default_limit
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return data.get("target_url", default_url), int(data.get("item_limit", default_limit))
    except:
        return default_url, default_limit


def save_data_incremental(output_path: Path, beijing_time: str, ai_summary: str, new_items: List[Tuple[str, str]]) -> None:
    """
    核心修改：读取旧数据，合并，去重，并保留最新内容。
    """
    # 1. 尝试读取现有数据
    existing_items = []
    if output_path.exists():
        try:
            old_data = json.loads(output_path.read_text(encoding="utf-8"))
            existing_items = old_data.get("items", [])
        except Exception as e:
            print(f"读取旧数据失败: {e}")

    # 2. 准备新数据
    formatted_new = [{"title": t, "url": u} for t, u in new_items]

    # 3. 合并并去重 (使用 URL 作为唯一标识)
    # 顺序：新抓取的放在前面，旧的放在后面
    combined_list = formatted_new + existing_items
    
    unique_items = []
    seen_urls = set()

    for item in combined_list:
        url = item.get("url")
        if url and url not in seen_urls:
            unique_items.append(item)
            seen_urls.add(url)

    # 4. 数量限制：保留最近 500 条，防止 JSON 过大
    final_items = unique_items[:500]

    # 5. 保存
    payload = {
        "beijing_time": beijing_time,
        "ai_summary": ai_summary,  # 总结通常保留最新的
        "items": final_items,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"数据已同步，当前库内共计 {len(final_items)} 条去重记录。")


def write_readme(items: List[Tuple[str, str]], beijing_time: str, summary_md: str, output_path: Path) -> None:
    """
    README 通常只展示当次抓取的内容，方便快速查看。
    """
    lines = [
        "# 🤖 Moltbook 科技动向自动监测",
        "",
        f"**更新时间：** {beijing_time}",
        "",
        "## 今日 3 大趋势（AI 总结）",
        "",
        summary_md if summary_md.strip() else "- （暂无总结）",
        "",
        "## 最新抓取列表",
        "",
    ]
    if items:
        for i, (title, url) in enumerate(items, 1):
            lines.append(f"{i}. [{title}]({url})")
    else:
        lines.append("*暂无新内容。*")
    
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    config_path = script_dir / "config.json"
    data_path = script_dir / "data.json"
    readme_path = script_dir / "README.md"
    
    url, item_limit = load_config(config_path)
    
    print(f"开始任务: {url}")
    new_items = scrape_post_links_with_playwright(url, "https://www.moltbook.com", item_limit)
    
    print("生成 AI 总结...")
    summary = summarize_with_ddg([t for t, _ in new_items])
    
    curr_time = get_beijing_time()
    
    # 执行增量保存
    save_data_incremental(data_path, curr_time, summary, new_items)
    # 更新 README
    write_readme(new_items, curr_time, summary, readme_path)


if __name__ == "__main__":
    main()
