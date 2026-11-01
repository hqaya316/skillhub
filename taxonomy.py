"""SkillHub 分类体系：大类定义 + 目录映射 + 关键词规则 + 作者来源识别。

设计原则：
1. 目录结构是最强信号（用户自己分的类通常是对的）
2. frontmatter 的 tags 是第二强信号
3. 简介中英关键词是兜底
4. 三者冲突时按权重打分，低分交给 LLM 复核
"""

# ---------------------------------------------------------------- 大类定义
# key = 内部 id；label = 中文名；color = 卡片色条；icon = 图标字符
CATEGORIES = {
    "dev":        ("编程开发",     "#3b82f6", "⌘"),
    "agent":      ("AI/智能体",    "#8b5cf6", "◈"),
    "mlops":      ("模型训练部署", "#6366f1", "⚙"),
    "data":       ("数据与金融",   "#10b981", "▤"),
    "design":     ("设计与可视化", "#ec4899", "✎"),
    "content":    ("内容与写作",   "#f59e0b", "✐"),
    "ecommerce":  ("电商运营",     "#ef4444", "◉"),
    "office":     ("办公与效率",   "#0ea5e9", "▦"),
    "comms":      ("邮件与通讯",   "#14b8a6", "✉"),
    "research":   ("研究与情报",   "#a855f7", "⌕"),
    "media":      ("媒体处理",     "#f97316", "▶"),
    "ops":        ("自动化与运维", "#64748b", "⚡"),
    "knowledge":  ("知识管理",     "#84cc16", "◧"),
    "life":       ("健康与生活",   "#22c55e", "❤"),
    "fun":        ("游戏与娱乐",   "#eab308", "◐"),
    "security":   ("安全与红队",   "#dc2626", "⚠"),
    "meta":       ("技能治理",     "#7c3aed", "◌"),
    "other":      ("未分类",       "#94a3b8", "○"),
}

# ---------------------------------------------------------------- 目录 → 大类
# 精确匹配路径中出现的目录段
DIR_MAP = {
    "software-development": "dev", "github": "dev", "devops": "ops",
    "autonomous-ai-agents": "agent", "mcp": "agent", "inference-sh": "agent",
    "mlops": "mlops", "data-science": "data",
    "vibe-trading": "data", "global-stock-data": "data",
    "creative": "design", "diagramming": "design",
    "business": "content", "media": "media", "social-media": "content",
    "ecommerce": "ecommerce",
    "productivity": "office", "note-taking": "knowledge",
    "email": "comms", "apple": "office",
    "research": "research", "web": "research",
    "health": "life", "smart-home": "life",
    "gaming": "fun", "gifs": "fun",
    "red-teaming": "security", "skill-governance": "meta",
}

# 单 skill 精确覆盖（针对直接躺在根目录、没有分类目录的 skill）
SKILL_MAP = {
    "apple-design": "design", "frontend-design": "design", "ui-ux-pro-max": "design",
    "brainstorming": "meta", "writing-skills": "meta", "using-superpowers": "meta",
    "dispatching-parallel-agents": "agent", "subagent-driven-development": "agent",
    "executing-plans": "dev", "writing-plans": "dev", "using-git-worktrees": "dev",
    "finishing-a-development-branch": "dev", "verification-before-completion": "dev",
    "receiving-code-review": "dev", "requesting-code-review": "dev",
    "test-driven-development": "dev", "systematic-debugging": "dev",
    "systematic-debugging": "dev",
    "ego-browser": "agent", "agent-reach": "agent", "notebooklm": "research",
    "cangjie-skill": "knowledge", "obsidian": "knowledge",
    "capture-to-vault": "knowledge", "cangjie-obsidian-redistill": "knowledge",
    "nihaixia": "life", "life-decision-guide": "life",
    "a-stock-data": "data", "yc-office-hours": "content",
    "skill-sublation": "meta", "skill-governance": "meta",
    "deepseek-harness-admin": "ops", "hermes-gateway-admin": "ops",
    "open-webui": "ops", "webhook-subscriptions": "ops", "kanban-orchestrator": "ops",
    "jupyter-live-kernel": "dev", "dspy": "agent", "godmode": "security",
    "huggingface-hub": "mlops", "comfyui": "design",
    "youtube-content": "media", "whisper-transcription": "media",
    "songwriting-and-ai-music": "content", "humanizer": "content",
    "blocked-page-recovery": "research", "himalaya": "comms", "xurl": "content",
    "spotify": "fun", "openhue": "life", "petdex": "fun",
    "fitness-nutrition": "life", "travel-planner": "life",
    # 苹果生态
    "apple-notes": "office", "apple-reminders": "office",
    "imessage": "comms", "findmy": "life",
    # 医疗 / 手术室工作流
    "intra-op-issue-sop": "life", "pre-op-checklist": "life",
    "handoff-summary": "life", "schedule-assistant": "life",
    "policy-search": "life", "policy-lookup": "life",
}

# ---------------------------------------------------------------- 关键词规则
# (正则, 大类, 权重)  —— 只做低权重辅助，避免误判
KEYWORDS = [
    # 编程开发
    (r"\b(refactor|debug|test|tdd|lint|git|pull request|\bpr\b|code review|"
     r"codebase|typescript|python|node\.js|compil|deploy|monorepo|api)\b", "dev", 1.0),
    (r"(编程|代码|重构|调试|单元测试|提交|分支|合并|构建)", "dev", 1.0),
    # AI / 智能体
    (r"\b(llm|agent|mcp|prompt|rag|orchestrat|delegat|subagent|tool call|"
     r"context window|skill hub|skill)\b", "agent", 0.9),
    (r"(智能体|代理|提示词|大模型|技能|编排)", "agent", 0.9),
    # MLOps
    (r"\b(fine-?tun|lora|qlora|training|inference|vllm|quantiz|gguf|"
     r"benchmark|eval harness|weights|hugging ?face|diffusion)\b", "mlops", 1.2),
    (r"(微调|训练|推理|量化|模型部署)", "mlops", 1.2),
    # 数据与金融
    (r"\b(stock|trading|backtest|portfolio|market|alpha|factor|option|"
     r"crypto|defi|financial|earnings|valuation|quant|tushare|yfinance|"
     r"akshare|candlestick|volatility)\b", "data", 1.2),
    (r"(股票|行情|回测|基金|估值|财报|交易|策略|因子|期权|加密货币|A股|港股|美股)", "data", 1.2),
    # 设计
    (r"\b(design|ui|ux|svg|diagram|mockup|figma|layout|typography|"
     r"infographic|comic|illustration|pixel art|canvas)\b", "design", 1.1),
    (r"(设计|界面|图表|视觉|排版|信息图|漫画|配色|像素画)", "design", 1.1),
    # 内容写作
    (r"\b(writ|copywrit|content|blog|newsletter|script|hook|headline|"
     r"thread|transcript|lyrics|humaniz|seo|公众号|小红书)\b", "content", 1.0),
    (r"(写作|文案|标题|内容|短视频|开头|结构|表达|演讲稿|歌词)", "content", 1.0),
    # 电商
    (r"\b(taobao|tmall|ecommerce|e-commerce|shopify|listing|conversion|"
     r"roi|ppc|crowd|product image)\b", "ecommerce", 1.5),
    (r"(淘宝|天猫|电商|直通车|万相台|达摩盘|主图|店铺|转化率|推广|选品)", "ecommerce", 1.5),
    # 办公效率
    (r"\b(docx|xlsx|pptx|excel|word|powerpoint|pdf|notion|airtable|linear|"
     r"calendar|email|inbox|meeting|task|todo|sheet)\b", "office", 1.1),
    (r"(文档|表格|演示|幻灯片|日历|会议|待办|周报|日程)", "office", 1.1),
    # 通讯
    (r"\b(imessage|sms|whatsapp|telegram|slack|discord|feishu|lark|wechat|"
     r"mail server|smtp|imap)\b", "comms", 1.2),
    (r"(微信|飞书|钉钉|邮件|短信|群发|消息)", "comms", 1.2),
    # 研究
    (r"\b(research|arxiv|paper|cit(e|ation)|monitor|feed|rss|crawl|scrape|"
     r"search|intelligence|competitor|polymarket)\b", "research", 0.9),
    (r"(研究|论文|检索|监控|抓取|情报|竞品|溯源|引用)", "research", 0.9),
    # 媒体
    (r"\b(video|audio|ffmpeg|whisper|transcri|music|spectrogram|subtitle|"
     r"image gen|midjourney|suno|spotify|youtube|gif)\b", "media", 1.2),
    (r"(视频|音频|字幕|转录|音乐|配音|图片生成|剪辑)", "media", 1.2),
    # 运维
    (r"\b(cron|schedule|deploy|server|docker|kubernetes|gateway|webhook|"
     r"systemd|launchd|backup|daemon|port|proxy|tailscale)\b", "ops", 1.1),
    (r"(定时任务|部署|服务器|网关|守护|备份|端口|代理|重启)", "ops", 1.1),
    # 知识管理
    (r"\b(obsidian|vault|note|zettelkasten|knowledge base|wiki|distill|"
     r"second brain|markdown note)\b", "knowledge", 1.2),
    (r"(知识库|笔记|双链|蒸馏|仓库|归档)", "knowledge", 1.2),
    # 生活健康
    (r"\b(health|fitness|nutrition|workout|recipe|sleep|medical|tcm|"
     r"chinese medicine|symptom|diet)\b", "life", 1.3),
    (r"(健康|养生|中医|健身|营养|睡眠|症状|体质|饮食|经方)", "life", 1.3),
    # 娱乐
    (r"\b(game|minecraft|pokemon|emulator|fun|mascot|pet|anime|meme)\b", "fun", 1.2),
    (r"(游戏|宠物|表情包|娱乐)", "fun", 1.2),
    # 安全
    (r"\b(security|jailbreak|red.?team|exploit|vulnerab|pentest|"
     r"prompt injection|bypass|abliterat)\b", "security", 1.4),
    (r"(安全|红队|越狱|漏洞|渗透|绕过)", "security", 1.4),
    # 元技能
    (r"\b(skill (author|govern|curat)|meta.?skill|self.?improv|"
     r"official document|writing (guidelines|docs))\b", "meta", 1.1),
]

# ---------------------------------------------------------------- 作者来源识别
# (匹配模式, 作者名, 来源类型)  —— 按顺序匹配，先命中先用
PUBLISHERS = [
    (r"cocoon",                      "Cocoon AI",           "第三方"),
    (r"anthropic|claude code",       "Anthropic",           "官方"),
    (r"nous research",               "Nous Research",       "官方"),
    (r"openai",                      "OpenAI",              "官方"),
    (r"obra|superpowers",            "obra/superpowers",    "社区"),
    (r"dontbesilent",                "dontbesilent",        "社区"),
    (r"jangviktor",                  "jangviktor-web",      "社区"),
    (r"hermes agent",                "Hermes 官方移植",      "官方"),
    (r"bergside|ui-ux-pro|21st\.dev", "ui-ux-pro-max 社区",  "社区"),
    (r"google",                      "Google",              "官方"),
    (r"vercel|stripe|linear",        "设计参考集",           "社区"),
]

SOURCE_LABEL = {
    "Hermes 主库": "本地主库",
    "Hermes Profile": "Profile 库",
    "Claude Code": "Claude Code",
    "手术室工作台": "自定义库",
}


def classify(name, description, tags, rel_dirs):
    """返回 (category, confidence, signals)。confidence 越高越可信。"""
    score = {}
    signals = []
    base = 0.0

    # 1) 目录映射
    for d in rel_dirs:
        if d in DIR_MAP:
            score[DIR_MAP[d]] = score.get(DIR_MAP[d], 0) + 4.0
            signals.append(f"目录:{d}")
            break

    # 2) skill 名精确映射
    if name in SKILL_MAP:
        score[SKILL_MAP[name]] = score.get(SKILL_MAP[name], 0) + 5.0
        signals.append("名称映射")

    # 3) 关键词
    import re
    hay = f"{name} {description} {' '.join(tags)}".lower()
    for pat, cat, w in KEYWORDS:
        hits = len(re.findall(pat, hay, flags=re.I))
        if hits:
            gained = w * min(hits, 3) * 0.6
            score[cat] = score.get(cat, 0) + gained

    if not score:
        return "other", 0.0, ["无信号"]

    cat, val = max(score.items(), key=lambda kv: kv[1])
    # 归一化：>=4 高置信，2~4 中，<2 低
    conf = min(val / 5.0, 1.0)
    if val >= 4:
        signals.append("高置信")
    elif val >= 2:
        signals.append("中置信")
    return cat, conf, signals


def detect_author(author_field, license_field, source_label, text_head):
    """返回 (作者名, 来源类型, 依据)。"""
    import re
    raw = " ".join(filter(None, [str(author_field or ""), str(license_field or "")])).lower()
    for pat, who, kind in PUBLISHERS:
        if re.search(pat, raw):
            return who, kind, "frontmatter"
    if author_field and str(author_field).strip():
        a = str(author_field).strip()
        return a[:60], "署名", "frontmatter"
    # 从正文里捞 github repo
    m = re.search(r"github\.com/([\w.-]+/[\w.-]+)", text_head or "")
    if m:
        return m.group(1), "仓库", "正文链接"
    if source_label in ("手术室工作台",):
        return "自建", "自建", "路径推断"
    if source_label == "Claude Code":
        return "obra/superpowers 等", "社区", "路径推断"
    return "未标注", "未知", "无"
