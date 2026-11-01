#!/usr/bin/env python3
"""SkillHub — 扫描本机所有 skill，自动分类 + 功能备注 + 作者溯源，生成可搜索仪表盘。

用法:
  python3 skillhub.py                # 全流程（扫描 → 分类 → LLM 补细分 → 生成仪表盘）
  python3 skillhub.py --no-llm       # 纯离线规则，秒出
  python3 skillhub.py --llm-only     # 只跑 LLM 补细分和备注，复用已有索引
  python3 skillhub.py --open         # 完成后自动打开浏览器

产物（全部在 output/，绝不修改任何 SKILL.md）:
  skills.json     全量索引
  index.html      仪表盘（单文件，可离线打开/分享）
"""
import argparse
import concurrent.futures as cf
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
sys.path.insert(0, HERE)

import taxonomy  # noqa: E402


# ============================================================ 扫描
def load_config():
    """优先本地 config.json；开源版把默认值放 config.example.json，照抄即可用。"""
    for name in ("config.json", "config.example.json"):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    raise SystemExit("缺 config.json：把 config.example.json 复制成 config.json 再改"
                     "（只写一个根目录也行）")


def expand_roots(cfg):
    roots, missing = [], []
    for r in cfg["roots"]:
        hits = sorted(glob.glob(os.path.expanduser(r["path"])))
        hits = [p for p in hits if os.path.isdir(p)]
        if not hits:
            missing.append((r["label"], r["path"]))
            continue
        for p in hits:
            roots.append((p, r["label"], r.get("primary", False), r.get("agent", r["label"])))
    return roots, missing


def _open_external(target):
    """macOS 用 open，Linux 用 xdg-open，其它平台只打印路径。"""
    if sys.platform == "darwin":
        cmd = ["open"]
    elif sys.platform.startswith("linux"):
        cmd = ["xdg-open"]
    else:
        cmd = None
    if cmd:
        try:
            subprocess.run(cmd + [target], check=False)
            return
        except OSError:
            pass
    print(f"仪表盘已生成，手动打开: {target}")


def parse_frontmatter(text):
    """返回 (dict, 正文)。容忍 yaml 解析失败，退回正则。"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end]
    body = text[end + 4:]
    try:
        import yaml
        data = yaml.safe_load(block)
        if isinstance(data, dict):
            return data, body
    except Exception:
        pass
    # 正则兜底
    data = {}
    for line in block.splitlines():
        m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if m:
            k, v = m.group(1), m.group(2).strip().strip("\"'")
            if v:
                data[k] = v
    return data, body


def clean_desc(d, limit=600):
    if not d:
        return ""
    d = re.sub(r"\s+", " ", str(d)).strip()
    return d[:limit]


def scan(root, label, primary, archive_markers, agent=None):
    found = []
    agent = agent or label
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames[:] = [d for d in dirnames
                       if d not in ("node_modules", ".git", "__pycache__", ".venv")]
        if "SKILL.md" not in filenames:
            continue
        path = os.path.join(dirpath, "SKILL.md")
        rel = os.path.relpath(dirpath, root)
        parts = [] if rel == "." else rel.split(os.sep)
        archived = any(p in archive_markers for p in parts) or any(
            p in archive_markers for p in root.split(os.sep))
        parts = [p for p in parts if p not in archive_markers]

        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue

        fm, body = parse_frontmatter(text)
        meta = fm.get("metadata") or {}
        hmeta = meta.get("hermes") if isinstance(meta, dict) else {}
        hmeta = hmeta if isinstance(hmeta, dict) else {}
        tags = hmeta.get("tags") or fm.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        name = str(fm.get("name") or (parts[-1] if parts else rel)).strip()
        desc = clean_desc(fm.get("description"))
        author_field = fm.get("author") or hmeta.get("author") or ""
        lic = fm.get("license") or ""
        head = text[:4000]

        author, kind, basis = taxonomy.detect_author(author_field, lic, label, head)

        h1 = ""
        m = re.search(r"^#\s+(.+)$", body, flags=re.M)
        if m:
            h1 = m.group(1).strip()[:120]

        # 目录内附属资源
        assets = []
        try:
            for sub in sorted(os.listdir(dirpath)):
                sp = os.path.join(dirpath, sub)
                if sub != "SKILL.md" and os.path.isdir(sp) and sub not in (".git", "node_modules"):
                    assets.append(sub)
        except OSError:
            pass

        size = len(text)
        lines = text.count("\n") + 1
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        if mtime < 978307200:            # 解包出来的文件常带 1970 时间戳，退回目录时间
            for cand in (os.path.dirname(path), dirpath):
                try:
                    m2 = os.path.getmtime(cand)
                except OSError:
                    continue
                if m2 >= 978307200:
                    mtime = m2
                    break

        cat, conf, signals = taxonomy.classify(name, desc, tags, parts)

        found.append({
            "id": f"{label}::{rel}",
            "name": name,
            "title": h1,
            "description": desc,
            "category": cat,
            "category_conf": round(conf, 2),
            "signals": signals,
            "author": author,
            "author_kind": kind,
            "author_basis": basis,
            "license": str(lic)[:40],
            "version": str(fm.get("version") or ""),
            "tags": [str(t) for t in tags][:12],
            "path": path,
            "dir": dirpath,
            "realpath": os.path.realpath(dirpath),
            "symlink": os.path.islink(dirpath) or os.path.islink(path),
            "rel": rel,
            "source": label,
            "agent": agent,
            "hash": __import__("hashlib").sha1(text.encode("utf-8", "replace")).hexdigest()[:12],
            "primary": primary,
            "archived": archived,
            "assets": assets,
            "lines": lines,
            "bytes": size,
            "mtime": time.strftime("%Y-%m-%d", time.localtime(mtime)),
            "note": "",          # LLM 一句话功能备注
            "note_source": "",
        })
    return found


def attach_bundles(skills):
    """识别「包里带的子 skill」：若某个 skill 目录的祖先目录本身也是 skill，
    则标记 bundle=祖先名（例如 vibe-trading 内置 75 个金融子技能）。"""
    by_dir = {s["dir"]: s for s in skills}
    for s in skills:
        cur = os.path.dirname(s["dir"])
        while cur and len(cur) > 1:
            if cur in by_dir and by_dir[cur]["id"] != s["id"]:
                parent = by_dir[cur]
                s["bundle"] = parent["name"]
                s["bundle_path"] = parent["path"]
                s["bundle_category"] = parent["category"]
                # 子技能继承父包的分类（包内通常同域）
                if s["category_conf"] < 0.85 and parent["category"] != "other":
                    s["category"] = parent["category"]
                # 子技能没署名时，归属到包作者
                if s["author"] in ("未标注", "") and parent["author"] not in ("未标注", ""):
                    s["author"] = parent["author"]
                    s["author_kind"] = parent["author_kind"]
                    s["author_basis"] = f"所属包 {parent['name']}"
                break
            cur = os.path.dirname(cur)
    return skills


def dedupe(skills):
    """标记跨 agent 重复，区分两种情况：

    - shared/copy：内容完全一致，只是装了多份（归集后共享同一实体）
    - divergent：同名但内容已经不一样，版本分叉，两份都要留着给人看
    """
    def mark_identical(groups):
        for group in groups:
            if len(group) < 2:
                continue
            # 公共库里的实体优先作为代表（它就是实体所在处）
            group.sort(key=lambda x: (x["agent"] != "公共库", x["archived"],
                                      not x["primary"], -x["bytes"]))
            keep = group[0]
            others = [s for s in group if s["id"] != keep["id"]]
            if others:
                agents_all = {s["agent"] for s in group}
                # 公共库代表实体，其余 agent 是引用；其余情况要求真的跨 ≥2 个软件
                cross = (agents_all - {"公共库"}) if keep["agent"] == "公共库" else agents_all
                cross_disc = cross - {keep["agent"]}
                if len(cross) > 1 or (keep["agent"] == "公共库" and len(cross) >= 1):
                    keep["shared_by"] = sorted(cross_disc)
                    keep["copies_count"] = len(group)
                    keep["dup_kind"] = "shared"
                    keep["dup_agents"] = sorted(cross)
                else:
                    # 同一个软件内重复（如 Hermes 主库 + profile），不算跨库共用
                    keep["dup_kind"] = "local"
                    keep["local_copies"] = len(group)
            for s in others:
                s["duplicate_of"] = s.get("duplicate_of") or keep["id"]
                s["dup_count"] = len(group)
                s["dup_kind"] = s.get("dup_kind") or "copy"
                s["dup_agents"] = sorted(agents_all)

    by_real, by_hash, by_name = {}, {}, {}
    for s in skills:
        by_real.setdefault(s["realpath"], []).append(s)
    mark_identical(by_real.values())

    # 内容完全相同（跨 agent 安装的同一份技能）
    for s in skills:
        by_hash.setdefault(s.get("hash") or s["realpath"], []).append(s)
    mark_identical(by_hash.values())

    # 名字级：同名但内容不同 → 版本分叉，不隐藏，两份都标出来
    for s in skills:
        if not s.get("duplicate_of"):
            by_name.setdefault(s["name"].lower(), []).append(s)
    for group in by_name.values():
        if len(group) < 2:
            continue
        agents = {s["agent"] for s in group}
        hashes = {s["hash"] for s in group}
        if len(agents) < 2:
            continue
        if len(hashes) > 1:
            for s in group:
                s["dup_kind"] = "divergent"
                s["dup_agents"] = sorted(agents)
                s["dup_versions"] = len(hashes)
                s["dup_count"] = len(group)
        else:
            mark_identical([group])
    # 每个 skill 记下「装在哪几个 agent」，筛选和计数都用它。
    # 一旦收进公共库，它就算公共库的：别的软件只是软链过去，不再算「装在那个软件里」，
    # 否则一个已经共享出去的 skill 会在侧栏同时挂在 Tutti / Accio 下面，看着像各装了一份。
    for s in skills:
        if (s.get("agent") or s["source"]) == "公共库":
            s["agents_all"] = ["公共库"]
        else:
            s["agents_all"] = sorted(set(s.get("dup_agents") or []) | {s.get("agent") or s["source"]})
    return skills


def keep_unreadable(skills, cache):
    """权限读不到的目录（如 ~/Documents）沿用上次索引，别让它们从面板里消失。"""
    if not os.path.exists(cache):
        return skills, 0
    try:
        prev = json.load(open(cache, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return skills, 0
    have = {s["dir"] for s in skills}
    added = 0
    for s in prev:
        if s["dir"] in have or not os.path.lexists(s["dir"]):
            continue
        try:
            os.listdir(s["dir"])
        except PermissionError:
            skills.append(s)
            added += 1
        except OSError:
            continue
    return skills, added


def reuse_notes(skills, cache):
    """把上一次索引里的 AI 备注/分类按内容 hash 搬回来。

    这样 --no-llm 重扫不会丢备注，日常刷新秒出；只有内容变了的 skill 才会重新问模型。
    """
    if not os.path.exists(cache):
        return 0
    try:
        with open(cache, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, json.JSONDecodeError):
        return 0
    by_hash = {p.get("hash"): p for p in prev if p.get("note") and p.get("hash")}
    n = 0
    for s in skills:
        p = by_hash.get(s.get("hash"))
        if not p or s.get("note"):
            continue
        s["note"] = p["note"]
        s["note_source"] = "cache"
        if p.get("category_llm"):
            s["category_llm"] = p["category_llm"]
        if p.get("category_final"):
            s["category_final"] = p["category_final"]
        if p.get("conflict"):
            s["conflict"] = True
        n += 1
    return n


# ============================================================ 自建标注
ORIGINS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "origins.json")

# 每个 agent 一种颜色，看颜色就知道这个 skill 是在哪建的
ORIGIN_COLORS = {
    "Hermes": "#f59e0b",            # 琥珀
    "Codex": "#a855f7",             # 紫
    "Claude Code": "#ec4899",       # 品红
    "Cursor": "#06b6d4",            # 青
    "DeepSeek Harness": "#10b981",  # 绿
    "cc-switch": "#f97316",         # 橙
}


def load_origins():
    try:
        with open(ORIGINS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"marks": {}}


def save_origins(origins):
    with open(ORIGINS, "w", encoding="utf-8") as f:
        json.dump(origins, f, ensure_ascii=False, indent=2)


BLOGGER_COLORS = {"推荐": "#14b8a6", "分享": "#0ea5e9"}


def apply_origins(skills, origins=None):
    """给 skill 打两类私有标记：

    - self_built：我自己建的 → 自建 · <agent>（每个 agent 一个颜色）
    - blogger：博主推荐 / 博主分享（两个标记，颜色区分）

    两类标记都按「名字」记，同一个 skill 装在几个 agent 里都显示同一标记。
    """
    origins = origins or load_origins()
    marks = {k.lower(): v for k, v in (origins.get("marks") or {}).items()}
    blogs = {k.lower(): v for k, v in (origins.get("blogger") or {}).items()}
    n = 0
    for s in skills:
        m = marks.get(s["name"].lower())
        if m:
            agent = m.get("agent") if isinstance(m, dict) else m
            s["self_built"] = True
            s["origin_agent"] = agent
            s["origin_color"] = ORIGIN_COLORS.get(agent, "#64748b")
            s["author"] = f"自建 · {agent}"
            s["author_kind"] = "自建"
            s["author_basis"] = "手动标注"
            n += 1
        b = blogs.get(s["name"].lower())
        if b:
            kind = b.get("kind") if isinstance(b, dict) else b
            s["blogger_kind"] = kind
            s["blogger_color"] = BLOGGER_COLORS.get(kind, "#64748b")
            n += 1
    return n


def reuse_notes_placeholder():
    pass
# ============================================================ LLM 补细分 + 备注
def llm_endpoint():
    """密钥来源：显式环境变量 → 旧版 Hermes 配置（兼容）→ 都没有就纯离线跑。

    开源版不假设用户装了 Hermes，也不把密钥写进仓库。
    """
    key = os.environ.get("SKILLHUB_LLM_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if key:
        base = (os.environ.get("SKILLHUB_LLM_BASE_URL")
                or "https://api.deepseek.com").rstrip("/")
        return key, base, os.environ.get("SKILLHUB_LLM_MODEL") or "deepseek-chat"
    cfg_path = os.path.expanduser(os.environ.get("SKILLHUB_HERMES_CONFIG", "")
                                  or "~/.hermes/config.yaml")
    try:
        import yaml
        c = yaml.safe_load(open(cfg_path, encoding="utf-8")) or {}
    except Exception:
        return None, None, None
    prov = (c.get("providers") or {}).get("deepseek") or {}
    key = prov.get("api_key") or os.environ.get("DEEPSEEK_API_KEY")
    base = (prov.get("base_url") or "https://api.deepseek.com").rstrip("/")
    if not key:
        return None, None, None
    return key, base, prov.get("model") or "deepseek-chat"


PROMPT = """你是 skill 库管理助手。下面是若干 AI skill 的元数据，每个有 idx 编号。

任务：对每个 skill 输出
1. cat：从下列固定分类中选一个最合适的（只输出英文 key）：
{cats}
2. note：一句中文功能备注，25 字以内，说清「什么时候用它做什么」，像目录注释，不要复述名字，不要形容词堆砌。
   - 简介原文是英文的，必须译成通顺中文，不要保留英文原句，专有名词（产品名、命令名）可保留。
   - 如果简介或标签里能看明确的来源（某公司/某开源项目/某平台官方），在 note 末尾用括号补上，如「…（Anthropic 官方）」「…（openai/skills）」；看不出就不写。
3. author：如果元数据里能看出作者/来源（如某平台名、某人名、某组织），输出该名称；否则输出空字符串。

严格输出 JSON 数组，不要 markdown 代码块，不要解释文字。
[{{"idx":0,"cat":"dev","note":"...","author":""}}]

待处理 skill：
{items}"""


def llm_batch(items, key, base, model, cats_desc):
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": PROMPT.format(cats=cats_desc, items=json.dumps(items, ensure_ascii=False, indent=1)),
        }],
        "temperature": 0.2,
        "max_tokens": 2000,
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = json.loads(r.read().decode())["choices"][0]["message"]["content"]
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    m = re.search(r"\[.*\]", raw, flags=re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return []


def run_llm(skills, cfg):
    lcfg = cfg.get("llm") or {}
    key, base, model = llm_endpoint()
    model = lcfg.get("model") or model
    if not key:
        print("  ⚠ 未找到 DeepSeek API key，跳过 LLM 步骤")
        return 0

    targets = [s for s in skills if not s["archived"] and not s.get("note")]
    if not targets:
        print("  LLM: 全部命中上次备注，跳过")
        return 0
    cats_desc = "\n".join(f"- {k}: {v[0]}" for k, v in taxonomy.CATEGORIES.items())
    bs = int(lcfg.get("batch_size", 15))

    # 优先级：低置信 → 未分类 → 其余（都要备注）
    targets.sort(key=lambda s: (s["category_conf"], s["category"] != "other"))
    batches = [targets[i:i + bs] for i in range(0, len(targets), bs)]
    print(f"  LLM: {len(targets)} 个 skill / {len(batches)} 批（{model}）")

    done = [0]
    lock = __import__("threading").Lock()

    def work(batch):
        items = [{
            "idx": i,
            "name": s["name"],
            "desc": (s["description"] or s["title"])[:300],
            "tags": s["tags"],
            "归入目录": s["rel"].split(os.sep)[0] if s["rel"] != "." else "",
            "当前判定": taxonomy.CATEGORIES.get(s["category"], ("",))[0],
        } for i, s in enumerate(batch)]
        try:
            res = llm_batch(items, key, base, model, cats_desc)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            print(f"  ✗ 批次失败: {e}")
            return
        except Exception as e:
            print(f"  ✗ 批次异常: {e}")
            return
        with lock:
            for r in res:
                i = r.get("idx")
                if not isinstance(i, int) or not (0 <= i < len(batch)):
                    continue
                s = batch[i]
                c = (r.get("cat") or "").strip()
                if c in taxonomy.CATEGORIES:
                    s["category_llm"] = c
                    # 规则高置信且不冲突时保留规则；否则采纳 LLM
                    if s["category_conf"] >= 0.8 and s["category"] != c:
                        s["category_final"] = s["category"]
                        s["conflict"] = True
                    else:
                        s["category_final"] = c
                note = (r.get("note") or "").strip()
                if note:
                    s["note"] = note[:60]
                    s["note_source"] = "llm"
                a = (r.get("author") or "").strip()
                if a and s["author"] in ("未标注", ""):
                    s["author"] = a[:40]
                    s["author_kind"] = "LLM 识别"
                    s["author_basis"] = "llm"
            done[0] += len(res)

    mw = int(lcfg.get("max_workers", 4))
    with cf.ThreadPoolExecutor(max_workers=mw) as ex:
        list(ex.map(work, batches))
    print(f"  LLM 完成: 回填 {done[0]} 条")
    return done[0]


# ============================================================ 构建仪表盘
def build_html(skills, cfg):
    stats = {
        "total": len(skills),
        "active": sum(1 for s in skills if not s["archived"]),
        "archived": sum(1 for s in skills if s["archived"]),
        "cats": {},
        "authors": {},
        "sources": {},
        "agents": {},
        "lines": sum(s["lines"] for s in skills),
        "dups": sum(1 for s in skills if s.get("duplicate_of")),
    }
    for s in skills:
        c = s.get("category_final") or s["category"]
        stats["cats"][c] = stats["cats"].get(c, 0) + 1
        stats["authors"][s["author"]] = stats["authors"].get(s["author"], 0) + 1
        stats["sources"][s["source"]] = stats["sources"].get(s["source"], 0) + 1
        stats["agents"][s.get("agent") or s["source"]] = stats["agents"].get(s.get("agent") or s["source"], 0) + 1

    # 默认视图口径：排除归档 / 跨库副本 / 包内子技能，侧栏数字与之一致
    visible = [s for s in skills
               if not s["archived"] and not s.get("duplicate_of") and not s.get("bundle")]
    stats["visible"] = len(visible)
    stats["cats_visible"] = {}
    stats["agents_visible"] = {}
    stats["agents_exclusive"] = {}
    stats["authors_visible"] = {}
    for s in visible:
        c = s.get("category_final") or s["category"]
        stats["cats_visible"][c] = stats["cats_visible"].get(c, 0) + 1
        # 按「装在哪」计数：共用技能在每个装过它的 agent 名下都算一份
        for a in (s.get("agents_all") or [s.get("agent") or s["source"]]):
            stats["agents_visible"][a] = stats["agents_visible"].get(a, 0) + 1
        a0 = s.get("agent") or s["source"]
        stats["agents_exclusive"][a0] = stats["agents_exclusive"].get(a0, 0) + 1
        stats["authors_visible"][s["author"]] = stats["authors_visible"].get(s["author"], 0) + 1

    # 包父节点记录子技能数量
    child_count = {}
    for s in skills:
        if s.get("bundle"):
            child_count[s["bundle"]] = child_count.get(s["bundle"], 0) + 1
    for s in skills:
        if s["name"] in child_count and not s.get("bundle"):
            s["bundle_children"] = child_count[s["name"]]
    stats["bundled"] = sum(1 for s in skills if s.get("bundle"))
    stats["dup_shared"] = sum(1 for s in skills if s.get("dup_kind") == "shared")
    stats["dup_divergent"] = sum(1 for s in skills if s.get("dup_kind") == "divergent")
    stats["dup_copies"] = sum(1 for s in skills if s.get("duplicate_of"))

    # 自建统计：按创建时的 agent 分组（只算去重后可见的，不含副本/包内）
    self_by_agent = {}
    for s in visible:
        if s.get("self_built"):
            a = s["origin_agent"]
            self_by_agent[a] = self_by_agent.get(a, 0) + 1
    stats["self_by_agent"] = self_by_agent
    stats["self_total"] = sum(self_by_agent.values())

    # 博主推荐 / 博主分享
    blog_by_kind = {}
    for s in visible:
        k = s.get("blogger_kind")
        if k:
            blog_by_kind[k] = blog_by_kind.get(k, 0) + 1
    stats["blogger_by_kind"] = blog_by_kind

    cats = [{"id": k, "label": v[0], "color": v[1], "icon": v[2],
             "count": stats["cats_visible"].get(k, 0),
             "count_all": stats["cats"].get(k, 0)}
            for k, v in taxonomy.CATEGORIES.items()]
    cats.sort(key=lambda c: -c["count"])

    data = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "stats": stats,
        "cats": cats,
        "skills": skills,
    }
    tpl_path = os.path.join(HERE, "template.html")
    with open(tpl_path, encoding="utf-8") as f:
        tpl = f.read()
    data["built_at"] = int(time.time() * 1000)   # 页面靠它判断索引有没有更新
    def atomic_write(path, text):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)          # 服务端永远读不到写了一半的文件

    html = tpl.replace("/*__DATA__*/", json.dumps(data, ensure_ascii=False))
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, "index.html")
    atomic_write(out, html)
    atomic_write(os.path.join(OUT, "data.json"), json.dumps(data, ensure_ascii=False))
    return out


# ============================================================ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="纯离线规则")
    ap.add_argument("--llm-only", action="store_true", help="复用已有 skills.json，只跑 LLM")
    ap.add_argument("--plan-pool", action="store_true", help="生成公共库归集方案（不修改文件）")
    ap.add_argument("--apply-pool", action="store_true", help="执行公共库归集（需再加 --yes）")
    ap.add_argument("--yes", action="store_true", help="确认执行 --apply-pool")
    ap.add_argument("--link-to", metavar="DIR", help="把公共库软链到指定 agent 的 skills 目录")
    ap.add_argument("--mark-self", metavar="NAME[,NAME]", help="把这些 skill 标为我自建（自动记当前所在 agent）")
    ap.add_argument("--as-agent", metavar="AGENT", help="配合 --mark-self：指定创建它的 agent，默认取该 skill 所在 agent")
    ap.add_argument("--unmark-self", metavar="NAME[,NAME]", help="取消自建标注")
    ap.add_argument("--mark-blogger", metavar="NAME[,NAME]", help="标为博主推荐/分享")
    ap.add_argument("--blogger-kind", default="推荐", choices=["推荐", "分享"], help="配合 --mark-blogger：推荐（默认）或分享")
    ap.add_argument("--add-to-pool", metavar="NAME[,NAME]", help="把指定 skill 收进公共库并软链回原处")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令")
    ap.add_argument("--open", action="store_true", help="完成后打开仪表盘")
    args = ap.parse_args()

    cfg = load_config()
    os.makedirs(OUT, exist_ok=True)
    cache = os.path.join(OUT, "skills.json")

    if args.llm_only and os.path.exists(cache):
        skills = json.load(open(cache, encoding="utf-8"))
        print(f"复用索引: {len(skills)} 个 skill")
    else:
        roots, missing = expand_roots(cfg)
        print(f"扫描 {len(roots)} 个根目录…")
        for label, path in missing:
            print(f"  ⚠ {label:<14} {path:<46} 不存在，跳过（软件没装/已改路径就改 config.json）")
        skills = []
        for root, label, primary, agent in roots:
            got = scan(root, label, primary, cfg.get("archive_markers", [".archive"]), agent)
            shown = root.replace(os.path.expanduser('~'), '~')
            warn = ""
            if not got and os.path.isdir(root):
                warn = "  ⚠ 读不到内容 —— 多半是 macOS 权限（系统设置 → 隐私与安全性 → 完全磁盘访问权限，把 /usr/bin/python3 加进去）"
            print(f"  {agent:<16} {shown:<48} {len(got):>3} 个{warn}")
            skills += got
        skills = attach_bundles(skills)
        skills, kept = keep_unreadable(skills, cache)
        if kept:
            print(f"  ⚠ {kept} 个 skill 目录因系统权限读不到，沿用上次索引"
                  f"（彻底解决：系统设置 → 完全磁盘访问权限 加上 /usr/bin/python3）")
        skills = dedupe(skills)
        n = apply_origins(skills)
        if n:
            print(f"  标注命中 {n} 条（自建 + 博主）")
        n = reuse_notes(skills, cache)
        if n:
            print(f"  复用上次备注 {n} 条（内容未变的 skill 不再请求模型）")

    if not args.no_llm:
        t0 = time.time()
        run_llm(skills, cfg)
        print(f"  LLM 耗时 {time.time() - t0:.0f}s")

    for s in skills:
        s.setdefault("category_final", s["category"])
        s["category_label"] = taxonomy.CATEGORIES.get(s["category_final"], ("未分类",))[0]
        s["category_color"] = taxonomy.CATEGORIES.get(s["category_final"], ("", "#94a3b8"))[1]

    # 稳定排序：分类顺序 → 名称
    order = {cid: i for i, (cid, _v) in enumerate(sorted(
        taxonomy.CATEGORIES.items(), key=lambda kv: kv[1][0]))}
    skills.sort(key=lambda s: (s["archived"], order.get(s["category_final"], 99), s["name"].lower()))

    with open(cache, "w", encoding="utf-8") as f:
        json.dump(skills, f, ensure_ascii=False, indent=1)

    out = build_html(skills, cfg)
    active = [s for s in skills if not s["archived"]]
    print(f"\n✓ {len(skills)} 个 skill（活跃 {len(active)} / 归档 {len(skills) - len(active)}）")
    print(f"✓ 索引: {cache}")
    print(f"✓ 仪表盘: {out}")

    if args.plan_pool or args.apply_pool:
        import pool as poolmod
        p = poolmod.plan(skills, cfg)
        paths = poolmod.save(p, cfg, OUT)
        st = p["stats"]
        print(f"\n公共库归集方案（{p['pool_path'].replace(os.path.expanduser('~'), '~')}）")
        print(f"  可归集 {st['groups']} 组 / {st['dirs']} 个目录，"
              f"释放约 {st['saved_bytes'] / 1024 / 1024:.1f} MB")
        if st["covered_children"]:
            pkgs = ", ".join(f"{k}({v})" for k, v in
                             sorted(p["covered_by_pkg"].items(), key=lambda kv: -kv[1])[:4])
            print(f"  连带共享包内子技能 {st['covered_children']} 个：{pkgs}")
        print(f"  涉及: {', '.join(st['agents'])}")
        print(f"  版本分叉（不处理）: {st['divergent']} 组")
        print(f"  方案: {paths['plan']}")
        print(f"  脚本: {paths['migrate']}  /  {paths['rollback']}")
        if args.apply_pool:
            if not args.yes:
                poolmod.apply(p, OUT, dry_run=True)
                print("\n  确认无误后执行:  python3 skillhub.py --apply-pool --yes")
            else:
                ok = poolmod.apply(p, OUT, dry_run=False)
                if ok:
                    print("\n✓ 归集完成。仪表盘已按新路径重建，回滚脚本在 output/pool-rollback.sh")

    if args.mark_self or args.unmark_self or args.mark_blogger:
        origins = load_origins()
        marks = origins.setdefault("marks", {})
        blogs = origins.setdefault("blogger", {})
        for raw in (args.mark_blogger or "").split(","):
            nm = raw.strip()
            if not nm:
                continue
            hit = next((s for s in skills if s["name"].lower() == nm.lower()), None)
            if not hit:
                print(f"  ⚠ 没找到 {nm}")
                continue
            blogs[hit["name"]] = {"kind": args.blogger_kind, "at": time.strftime("%Y-%m-%d")}
            print(f"  ✓ {hit['name']} → 博主{args.blogger_kind}")
        if args.mark_self:
            for raw in args.mark_self.split(","):
                nm = raw.strip()
                if not nm:
                    continue
                hit = next((s for s in skills if s["name"].lower() == nm.lower()
                            and not s["archived"]), None)
                if not hit:
                    print(f"  ⚠ 没找到 {nm}")
                    continue
                agent = args.as_agent or hit["agent"]
                marks[hit["name"]] = {"agent": agent, "at": time.strftime("%Y-%m-%d")}
                print(f"  ✓ {hit['name']} → 自建 · {agent}")
        for raw in (args.unmark_self or "").split(","):
            nm = raw.strip()
            if not nm:
                continue
            for k in [k for k in marks if k.lower() == nm.lower()]:
                marks.pop(k, None)
                print(f"  ✓ 取消标注 {nm}")
        save_origins(origins)
        apply_origins(skills, origins)
        with open(cache, "w", encoding="utf-8") as f:   # 索引也要同步，否则服务端看不到标注
            json.dump(skills, f, ensure_ascii=False)
        out = build_html(skills, cfg)   # 标注变了要重写仪表盘
        print(f"✓ 仪表盘已更新: {out}")

    if args.link_to:
        import pool as poolmod
        poolmod.link_to((cfg.get("pool") or {}).get("path") or "~/.agent-skills",
                        args.link_to, dry_run=args.dry_run)

    if args.add_to_pool:
        import pool as poolmod
        names = [n for n in args.add_to_pool.split(",") if n.strip()]
        print(f"\n收进公共库{'（预览）' if args.dry_run else ''}：")
        poolmod.add_to_pool(skills, names, cfg, dry_run=args.dry_run or not args.yes)
        if not args.yes:
            print("\n  加 --yes 才会真正移动文件")

    if args.open and not os.environ.get("SKILLHUB_NO_OPEN"):
        _open_external(out)


if __name__ == "__main__":
    main()
