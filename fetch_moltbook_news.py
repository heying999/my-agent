#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Moltbook 科技动向抓取脚本（Playwright 版）
访问 https://www.moltbook.com/m/ai，等待 /post/ 内容加载后抓取标题与链接，写入 README.md
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
    使用 duckduckgo_search 的 DDGS().chat()（无需 API Key）总结今日 3 大趋势。

    将抓取的标题传入，固定使用 'gpt-4o-mini' 模型，返回 Markdown 列表文本。
    """
    if not titles:
        return ""

    def _clean_title(t: str) -> str:
        t = re.sub(r"\s+", " ", (t or "").strip())
        # 去掉类似 Reddit 风格的噪音前缀/后缀（不影响 README 中原始标题展示）
        t = re.sub(r"^▲\s*\d+\s*▼\s*Posted by\s+u/\S+\s+\S+\s+ago\s+", "", t, flags=re.I)
        t = re.sub(r"\s*💬\s*\d+\s*comments?\s*$", "", t, flags=re.I)
        return t.strip()

    cleaned = [_clean_title(t) for t in titles]
    cleaned = [t for t in cleaned if t]

    def _fallback_three_trends(ts: List[str]) -> str:
        tl = " ".join(t.lower() for t in ts)
        themes = [
            (
                "AI 代理框架的工程化与落地挑战",
                ["framework", "agent framework", "limitations", "production", "demo", "edge case", "error handling", "prod"],
                "讨论从 Demo 走向生产的鸿沟：稳定性、边界条件、错误处理与可维护性成为核心。",
            ),
            (
                "多智能体协作、通信与记忆基础设施",
                ["multi-agent", "agent-to-agent", "coordination", "communication", "bridge", "memory", "shared memory", "pheromone", "colony", "protocol"],
                "围绕多智能体协作的通信协议、共享记忆与群体协调机制的探索明显增多。",
            ),
            (
                "自治与信任：代理如何在适当时机行动/不行动",
                ["autonomy", "trust", "permission", "act", "useful", "wait", "value", "decision"],
                "关注代理的自治边界与人机信任关系：何时主动、何时克制，直接影响长期可用性。",
            ),
            (
                "API 化、结构化数据与基础设施思维",
                ["api", "endpoint", "json", "shell", "infrastructure"],
                "更偏向用 API/结构化数据直连系统，强调“可组合”的基础设施而非界面层表象。",
            ),
            (
                "量化/加密风险管理与仓位数学",
                ["kelly", "crypto", "position sizing", "portfolio", "trade"],
                "少量内容聚焦交易风险控制与仓位管理，用数学约束波动与回撤。",
            ),
        ]

        scored = []
        for name, keys, desc in themes:
            score = sum(1 for k in keys if k in tl)
            scored.append((score, name, desc))
        scored.sort(key=lambda x: x[0], reverse=True)

        picked = [x for x in scored if x[0] > 0][:3]
        if len(picked) < 3:
            # 兜底补齐 3 条
            for x in scored:
                if x not in picked:
                    picked.append(x)
                if len(picked) >= 3:
                    break

        return "\n".join(f"- **{name}**：{desc}" for _, name, desc in picked[:3])

    # 控制输入长度，避免触发会话/长度限制/限流
    cleaned = cleaned[:25]
    cleaned = [t[:220] for t in cleaned]

    prompt = (
        "你是科技资讯编辑。请基于以下标题列表，用中文总结“今日 3 大趋势”。\n"
        "要求：\n"
        "1) 严格输出 3 条；\n"
        "2) 使用 Markdown 无序列表（每条以 - 开头）；\n"
        "3) 每条 1-2 句，提炼主题，不要复述点赞/作者/评论数等噪音；\n"
        "4) 不要输出除这 3 条以外的任何内容。\n\n"
        "标题列表：\n"
        + "\n".join(f"- {t}" for t in cleaned)
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    last_err = None
    for attempt in range(3):
        try:
            raw = DDGS(headers=headers, timeout=60).chat(prompt, model="gpt-4o-mini", timeout=60).strip()
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            bullets = []
            for ln in lines:
                if ln.startswith(("-", "•")):
                    bullets.append("- " + ln.lstrip("-•").strip())
                elif re.match(r"^\d+[.)]\s+", ln):
                    bullets.append("- " + re.sub(r"^\d+[.)]\s+", "", ln).strip())
            bullets = bullets[:3]
            if len(bullets) == 3:
                return "\n".join(bullets)
            # 输出不符合要求则走本地兜底
            return _fallback_three_trends(cleaned)
        except Exception as e:
            last_err = e
            # 418/限流时做简单退避重试
            time.sleep(1 + attempt * 2)

    # 多次失败：给出本地兜底（仍保证 3 条）
    _ = last_err  # 仅保留以便未来调试
    return _fallback_three_trends(cleaned)


def scrape_post_links_with_playwright(url: str, base_url: str, item_limit: int) -> List[Tuple[str, str]]:
    """
    使用 Playwright 打开页面，等待带 /post/ 的链接出现，抓取所有标题和链接。
    过滤重复与导航链接。
    """
    results: List[Tuple[str, str]] = []
    seen_urls: set = set()
    seen_titles_norm: set = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # 等待带有 /post/ 的链接出现（最多等 20 秒）
            page.wait_for_selector('a[href*="/post/"]', timeout=20000)

            # 再给一点时间让列表稳定
            page.wait_for_timeout(2000)

            # 获取所有包含 /post/ 的链接
            links = page.query_selector_all('a[href*="/post/"]')

            for link in links:
                href = link.get_attribute("href")
                if not href or "/post/" not in href:
                    continue

                text = link.inner_text().strip()
                text = re.sub(r"\s+", " ", text)

                # 过滤空标题或过短
                if not text or len(text) < 2:
                    continue

                # 排除导航类文本
                text_lower = text.lower()
                if any(nav in text_lower for nav in NAV_TEXT_BLACKLIST):
                    continue

                full_url = urljoin(base_url, href)
                # 去重：按 URL
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                # 去重：按规范化标题（避免同一文章不同格式重复）
                title_norm = text.strip().lower()[:80]
                if title_norm in seen_titles_norm:
                    continue
                seen_titles_norm.add(title_norm)

                results.append((text.strip(), full_url))
                if len(results) >= item_limit:
                    break

        except Exception as e:
            print(f"抓取过程出错: {e}")
        finally:
            browser.close()

    return results


def load_config(config_path: Path) -> Tuple[str, int]:
    """
    读取 config.json，获取 target_url 与 item_limit。
    文件不存在或字段缺失/非法时使用默认值。
    """
    default_url = "https://www.moltbook.com/m/ai"
    default_limit = 30

    if not config_path.exists():
        return default_url, default_limit

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return default_url, default_limit

    target_url = data.get("target_url", default_url)
    if not isinstance(target_url, str) or not target_url.strip():
        target_url = default_url

    item_limit = data.get("item_limit", default_limit)
    try:
        item_limit_int = int(item_limit)
    except Exception:
        item_limit_int = default_limit

    # 合理范围保护
    item_limit_int = max(1, min(item_limit_int, 200))
    return target_url, item_limit_int


def save_data_json(output_path: Path, beijing_time: str, ai_summary: str, items: List[Tuple[str, str]]) -> None:
    payload = {
        "beijing_time": beijing_time,
        "ai_summary": ai_summary,
        "items": [{"title": t, "url": u} for t, u in items],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_readme(items: List[Tuple[str, str]], beijing_time: str, summary_md: str, output_path: Path) -> None:
    """将标题、时间和条目列表写入 README.md。"""
    lines = [
        "# 🤖 Moltbook 科技动向自动监测",
        "",
        f"**更新时间：** {beijing_time}",
        "",
        "## 今日 3 大趋势（DuckDuckGo AI 总结）",
        "",
    ]

    if summary_md and summary_md.strip():
        lines.extend(summary_md.strip().splitlines())
    else:
        lines.append("- （暂无总结）")

    lines.extend(
        [
            "",
            "## 最新动向",
            "",
        ]
    )
    if items:
        for i, (title, url) in enumerate(items, 1):
            lines.append(f"{i}. [{title}]({url})")
        lines.append("")
    else:
        lines.append("*暂无解析到带链接的条目（可能超时或页面无 /post/ 内容）。*")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入: {output_path.absolute()}")


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    config_path = script_dir / "config.json"
    url, item_limit = load_config(config_path)
    base_url = "https://www.moltbook.com"
    output_path = script_dir / "README.md"
    data_path = script_dir / "data.json"

    print("正在使用 Playwright 抓取页面...")
    items = scrape_post_links_with_playwright(url, base_url, item_limit=item_limit)

    titles = [t for t, _ in items]
    print("正在生成 DuckDuckGo AI 总结...")
    summary_md = summarize_with_ddg(titles)

    beijing_time = get_beijing_time()
    write_readme(items, beijing_time, summary_md, output_path)
    save_data_json(data_path, beijing_time, summary_md, items)


if __name__ == "__main__":
    main()
