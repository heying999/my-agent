# ... 之前的导入保持不变 ...

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
                # 等待卡片加载
                page.wait_for_selector('a[href*="/post/"]', timeout=15000)
                
                # 抓取所有包含链接的容器
                items = page.query_selector_all('div.flex.flex-col.gap-1') # 针对 Moltbook 结构优化
                if not items: # 兜底逻辑
                    items = page.query_selector_all('a[href*="/post/"]')

                count = 0
                for item in items:
                    raw_text = item.inner_text().strip()
                    href_el = item.query_selector('a[href*="/post/"]') if hasattr(item, 'query_selector') else item
                    href = href_el.get_attribute("href") if href_el else None
                    
                    if not href or not raw_text: continue

                    # --- 【核心算法：精准提取】 ---
                    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
                    
                    # 1. 提取标题：通常在 "Posted by" 之后或者是第一行
                    # 我们尝试用正则过滤掉点赞数和作者信息
                    clean_title = lines[0]
                    for line in lines:
                        if "Posted by" in line: continue
                        if "ago" in line: continue
                        if line.startswith("▲") or line.startswith("^"): continue
                        if "comments" in line.lower(): continue
                        clean_title = line # 找到最像标题的那一行
                        break
                    
                    # 2. 提取热度 (点赞数 & 评论数)
                    score = re.search(r'[▲\^]\s*(\d+)', raw_text)
                    comments = re.search(r'(\d+)\s*comments', raw_text.lower())
                    
                    score_val = score.group(1) if score else "0"
                    comment_val = comments.group(1) if comments else "0"
                    
                    # 过滤掉过短的杂讯
                    if len(clean_title) < 5: continue

                    all_results.append({
                        "title": clean_title,
                        "url": urljoin("https://www.moltbook.com", href),
                        "category": cat,
                        "hot_info": f"🔥{score_val} | 💬{comment_val}" # 保存热度信息
                    })
                    count += 1
                    if count >= limit: break
                
                print(f"✅ {cat} 获取 {count} 条题目。", flush=True)
            except Exception as e:
                print(f"❌ {cat} 抓取异常: {e}", flush=True)
        browser.close()
    return all_results

# ... incremental_translate 函数无需变动，因为它只处理 title 字段 ...
