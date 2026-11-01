"""公共 Skill 库归集器。

问题：同一个第三方 skill 被重复安装在 Hermes / Codex / Claude / Cursor 各自的
skills 目录里，升级时要去改 N 份，久而久之各库版本分叉。

方案：内容完全一致的那份只保留在公共库，各 agent 目录里放一个软链接指过去。
路径不变，agent 照常读，但全机只有一份实体。

默认只生成方案与脚本（output/pool-plan.md、pool-migrate.sh、pool-rollback.sh），
不修改任何文件；确认后才 --apply 执行。
"""
import os
import subprocess
import time

HOME = os.path.expanduser("~")


def _tilde(p):
    return "~" + p[len(HOME):] if p.startswith(HOME) else p


def _shq(s):
    """shell 单引号安全化"""
    return "'" + str(s).replace("'", "'\\''") + "'"


def plan(skills, cfg):
    """返回归集方案：{groups:[...], skip:{...}, stats:{...}}"""
    pcfg = cfg.get("pool") or {}
    pool_path = os.path.expanduser(pcfg.get("path") or "~/.agent-skills")
    priority = pcfg.get("priority") or []
    skip_agents = set(pcfg.get("skip_agents") or [])
    min_copies = int(pcfg.get("min_copies", 2))

    def prio(agent):
        return priority.index(agent) if agent in priority else len(priority)

    # 只考虑实体目录（软链已归集的不用管）、非归档、非包内子技能
    candidates = [
        s for s in skills
        if not s["archived"]
        and not s.get("bundle")
        and not s["symlink"]
        and s.get("agent") not in skip_agents
    ]

    groups = {}
    for s in candidates:
        groups.setdefault(s.get("hash") or s["id"], []).append(s)

    doable, divergent = [], []
    for h, g in groups.items():
        agents = {s["agent"] for s in g}
        # 去掉同目录的重复条目
        uniq = {s["realpath"]: s for s in g}
        g = list(uniq.values())
        if len(g) < min_copies or len(agents) < 2:
            continue
        g.sort(key=lambda s: (prio(s["agent"]), not s["primary"], -s["bytes"]))
        keep = g[0]
        doable.append({
            "hash": h,
            "name": keep["name"],
            "dirname": os.path.basename(keep["dir"]),
            "canonical": keep,
            "copies": g,
            "agents": sorted({s["agent"] for s in g}, key=prio),
            "lines": keep["lines"],
            "bytes": keep["bytes"],
        })

    doable.sort(key=lambda x: (x["agents"], x["name"].lower()))

    # 归集包之后，包内子技能随之共享，单独计数（不参与 move）
    doable_names = {g["name"] for g in doable}
    covered_children = [
        s for s in skills
        if not s["archived"] and s.get("bundle") in doable_names and not s["symlink"]
    ]
    covered_by_pkg = {}
    for s in covered_children:
        covered_by_pkg[s["bundle"]] = covered_by_pkg.get(s["bundle"], 0) + 1

    # 同名但内容不同 → 版本可能已分叉，不动，只报告
    by_name = {}
    for s in candidates:
        by_name.setdefault(s["name"].lower(), []).append(s)
    for name, g in by_name.items():
        hashes = {s.get("hash") for s in g}
        agents = {s["agent"] for s in g}
        if len(hashes) > 1 and len(agents) > 1:
            divergent.append({
                "name": name,
                "versions": [
                    {"agent": s["agent"], "hash": s["hash"], "path": _tilde(s["dir"]),
                     "mtime": s["mtime"], "lines": s["lines"], "bytes": s["bytes"]}
                    for s in sorted(g, key=lambda x: prio(x["agent"]))
                ],
            })

    # 目录名冲突检查
    used = {}
    for item in doable:
        base = item["dirname"]
        target = os.path.join(pool_path, base)
        n = 2
        while target in used:
            target = os.path.join(pool_path, f"{base}-{n}")
            n += 1
        used[target] = item
        item["target"] = target

    saved = sum(item["bytes"] * (len(item["copies"]) - 1) for item in doable)
    return {
        "pool_path": pool_path,
        "groups": doable,
        "divergent": divergent,
        "covered_by_pkg": covered_by_pkg,
        "stats": {
            "groups": len(doable),
            "dirs": sum(len(i["copies"]) for i in doable),
            "saved_bytes": saved,
            "divergent": len(divergent),
            "covered_children": len(covered_children),
            "agents": sorted({a for i in doable for a in i["agents"]}, key=prio),
        },
    }


def write_scripts(p, cfg):
    """生成计划文档 + 迁移/回滚脚本。返回三个文件路径。"""
    pool_path = p["pool_path"]
    groups = p["groups"]

    # ---------- 迁移脚本 ----------
    lines = [
        "#!/bin/bash",
        "# 公共 Skill 库归集脚本 — 由 SkillHub 生成，执行前请先读 pool-plan.md",
        f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M')}",
        f"# 归集 {p['stats']['groups']} 组，涉及 {p['stats']['dirs']} 个目录",
        "set -euo pipefail",
        "",
        'if [ "${1:-}" != "--yes" ]; then',
        '  echo "这会移动并替换以下目录。确认无误后加 --yes 执行：";',
        '  echo "  bash pool-migrate.sh --yes"',
        "  exit 1",
        "fi",
        "",
        f"mkdir -p {_shq(pool_path)}",
        "",
    ]
    for i, g in enumerate(groups, 1):
        lines.append(f"# [{i}/{len(groups)}] {g['name']}  ←  {', '.join(g['agents'])}")
        src = g["canonical"]["dir"]
        tgt = g["target"]
        lines.append(f"mv {_shq(src)} {_shq(tgt)}")
        # 规范副本原位也换成软链
        lines.append(f"ln -s {_shq(tgt)} {_shq(src)}")
        for c in g["copies"]:
            if c["realpath"] == g["canonical"]["realpath"]:
                continue
            lines.append(f"rm -rf {_shq(c['dir'])}")
            lines.append(f"ln -s {_shq(tgt)} {_shq(c['dir'])}")
        lines.append("")

    # ---------- 回滚脚本 ----------
    rb = [
        "#!/bin/bash",
        "# 公共 Skill 库回滚 — 撤销 pool-migrate.sh，恢复成各自独立的副本",
        "set -euo pipefail",
        "",
    ]
    for i, g in enumerate(reversed(groups), 1):
        tgt = g["target"]
        src = g["canonical"]["dir"]
        rb.append(f"# [{i}] {g['name']}")
        for c in g["copies"]:
            rb.append(f"[ -L {_shq(c['dir'])} ] && rm -f {_shq(c['dir'])} || true")
        rb.append(f"mv {_shq(tgt)} {_shq(src)}")
        for c in g["copies"]:
            if c["realpath"] == g["canonical"]["realpath"]:
                continue
            rb.append(f"cp -R {_shq(src)} {_shq(c['dir'])}")
        rb.append("")

    # ---------- 方案文档 ----------
    md = [
        "# 公共 Skill 库归集方案",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M')}",
        f"- 公共库路径：`{_tilde(pool_path)}`",
        f"- 可归集：**{p['stats']['groups']} 组** / {p['stats']['dirs']} 个目录"
        f"，释放约 **{p['stats']['saved_bytes'] / 1024 / 1024:.1f} MB**",
        f"- 涉及软件：{', '.join(p['stats']['agents']) or '—'}",
        f"- 版本分叉（不同步，需人工判断）：{p['stats']['divergent']} 组",
        "",
        "## 归集清单",
        "",
        "| # | Skill | 装在哪些软件 | 保留自 | 行数 |",
        "|---|-------|-------------|--------|------|",
    ]
    for i, g in enumerate(groups, 1):
        md.append(f"| {i} | `{g['name']}` | {' · '.join(g['agents'])} | "
                  f"{g['canonical']['agent']} | {g['lines']} |")
    md += ["", "## 执行方式", "",
           "```bash",
           "bash pool-migrate.sh --yes     # 执行归集",
           "bash pool-rollback.sh          # 出问题就回滚",
           "```", ""]

    if p.get("covered_by_pkg"):
        md += ["## 包内子技能（随整包共享，无需单独归集）", ""]
        for pkg, n in sorted(p["covered_by_pkg"].items(), key=lambda kv: -kv[1]):
            md.append(f"- `{pkg}`：内含 {n} 个子技能，归集该包后自动共享")
        md.append("")

    if p["divergent"]:
        md += ["## ⚠ 同名但内容不同（未纳入归集）", "",
               "这些 skill 在不同软件里版本已经分叉，归集前建议先确认用哪个版本：", ""]
        for d in p["divergent"]:
            md.append(f"### `{d['name']}`")
            md.append("")
            md.append("| 软件 | 行数 | 大小 | 更新时间 | 路径 |")
            md.append("|------|------|------|----------|------|")
            for v in d["versions"]:
                md.append(f"| {v['agent']} | {v['lines']} | {v['bytes'] / 1024:.0f}KB | {v['mtime']} | `{v['path']}` |")
            md.append("")

    return md, lines, rb


def save(p, cfg, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    md, mig, rb = write_scripts(p, cfg)
    paths = {}
    with open(os.path.join(out_dir, "pool-plan.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    paths["plan"] = os.path.join(out_dir, "pool-plan.md")
    for name, content, mode in (("pool-migrate.sh", mig, 0o755), ("pool-rollback.sh", rb, 0o755)):
        pth = os.path.join(out_dir, name)
        with open(pth, "w", encoding="utf-8") as f:
            f.write("\n".join(content) + "\n")
        os.chmod(pth, mode)
        paths[name.split(".")[0].replace("pool-", "")] = pth
    return paths


def add_to_pool(skills, names, cfg, dry_run=True):
    """把指定名字的 skill 收进公共库（即使只装了一个软件）。

    用户以后装第三方 skill 时可以直接 --add-to-pool，让它一进库就是共享状态。
    多份内容不同时按最新修改的那份为准，并在输出里明确提示。
    """
    pcfg = cfg.get("pool") or {}
    pool_path = os.path.expanduser(pcfg.get("path") or "~/.agent-skills")
    priority = pcfg.get("priority") or []

    def prio(a):
        return priority.index(a) if a in priority else len(priority)

    wanted = {n.strip().lower() for n in names if n.strip()}
    by_name = {}
    for s in skills:
        if s["archived"] or s.get("bundle") or s["symlink"]:
            continue
        if s["name"].lower() in wanted or os.path.basename(s["dir"]).lower() in wanted:
            by_name.setdefault(s["name"].lower(), []).append(s)

    missing = wanted - set(by_name)
    if missing:
        print(f"⚠ 没找到: {', '.join(sorted(missing))}")

    done = []
    for name, g in by_name.items():
        uniq = {s["realpath"]: s for s in g}
        g = list(uniq.values())
        hashes = {s["hash"] for s in g}
        # 内容不同 → 取更新时间最新的那份（按文件系统精确时间）
        def _mt(s):
            try:
                return os.path.getmtime(os.path.join(s["dir"], "SKILL.md"))
            except OSError:
                try:
                    return os.path.getmtime(s["dir"])
                except OSError:
                    return 0.0
        g.sort(key=_mt, reverse=True)
        keep = g[0]
        target = os.path.join(pool_path, os.path.basename(keep["dir"]))
        if os.path.lexists(target) and not dry_run:
            print(f"  跳过 {name}：公共库里已有同名目录 {target}")
            continue
        print(f"  {'[预览] ' if dry_run else ''}{name}"
              f"  ←  {', '.join(sorted({s['agent'] for s in g}, key=prio))}"
              f"（保留 {keep['agent']} / {keep['mtime']}"
              f"{'，⚠ 各库内容不同' if len(hashes) > 1 else ''}）")
        if not dry_run:
            os.makedirs(pool_path, exist_ok=True)
            subprocess.run(["mv", keep["dir"], target], check=True)
            for s in g:
                if s["realpath"] == keep["realpath"] or not os.path.lexists(s["dir"]):
                    os.symlink(target, s["dir"])
                else:
                    subprocess.run(["rm", "-rf", s["dir"]], check=True)
                    os.symlink(target, s["dir"])
            if not os.path.lexists(keep["dir"]):
                os.symlink(target, keep["dir"])
        done.append(name)
    return done


def link_to(pool_path, target_dir, dry_run=False):
    """把公共库里的每个 skill 软链到某个 agent 的 skills 目录。

    用于新装一个 agent（例如 DeepSeek Harness）后，一条命令挂上整库，
    不必重新下载安装。已存在的条目一律跳过，不覆盖。
    """
    pool_path = os.path.expanduser(pool_path)
    target_dir = os.path.expanduser(target_dir)
    if not os.path.isdir(pool_path):
        print(f"✗ 公共库不存在: {pool_path}（先跑 --apply-pool 归集）")
        return
    entries = [d for d in sorted(os.listdir(pool_path))
               if os.path.isdir(os.path.join(pool_path, d)) and not d.startswith(".")]
    created, skipped = [], []
    for name in entries:
        dst = os.path.join(target_dir, name)
        if os.path.lexists(dst):
            skipped.append(name)
            continue
        created.append(name)
        if not dry_run:
            os.makedirs(target_dir, exist_ok=True)
            os.symlink(os.path.join(pool_path, name), dst)
    verb = "将挂载" if dry_run else "已挂载"
    print(f"\n{verb} {len(created)} 个 skill → {_tilde(target_dir)}")
    print(f"  跳过已存在 {len(skipped)} 个")
    if dry_run and created:
        print("  前 10 个:", ", ".join(created[:10]))
    return created


def apply(p, out_dir, dry_run=False):
    """执行归集。dry_run 只打印。"""
    script = os.path.join(out_dir, "pool-migrate.sh")
    if dry_run:
        cmds = [l for l in open(script, encoding="utf-8") if l.startswith(("mv ", "ln ", "rm ", "mkdir "))]
        print(f"\n[预览] 将执行 {len(cmds)} 条命令：")
        for c in cmds[:20]:
            print("   ", c.rstrip())
        if len(cmds) > 20:
            print(f"    … 另 {len(cmds) - 20} 条")
        return False
    r = subprocess.run(["bash", script, "--yes"], capture_output=True, text=True)
    print(r.stdout[-3000:] if r.stdout else "")
    if r.returncode != 0:
        print("✗ 执行失败：\n", r.stderr[-3000:])
        print("可运行 bash pool-rollback.sh 回滚")
        return False
    return True
