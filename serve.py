#!/usr/bin/env python3
"""本地面板服务 — 给仪表盘补上两个真实动作：

  · 在 Finder 显示（open -R）
  · 移到废纸篓（走 Finder，可恢复）

浏览器里的 file:// 页面没有文件系统权限，所以这两个按钮必须由本机服务执行。
只监听 127.0.0.1，并且每次启动生成一次性 token，前端请求必须带上。
"""
import hashlib
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
INDEX = os.path.join(OUT, "index.html")
LOG_DIR = os.path.expanduser(os.environ.get("SKILLHUB_LOG_DIR", "~/.skillhub"))
LOG_PATH = os.path.join(LOG_DIR, "skillhub.log")


def _log_path():
    """审计/重扫日志放用户目录（/tmp 是全局可写目录，不该往里写）。"""
    try:
        os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
    except OSError:
        return None
    return LOG_PATH


def open_external(target, reveal=False):
    """打开 URL / 在文件管理器中定位路径；非 macOS 走 xdg-open。"""
    if sys.platform == "darwin":
        args = ["open", "-R", target] if reveal else ["open", target]
    elif reveal:
        args = ["xdg-open", os.path.dirname(target) or target]
    else:
        args = ["xdg-open", target]
    try:
        subprocess.run(args, check=False)
    except OSError:
        print(f"请手动打开: {target}")
def _token():
    """token 落盘复用：LaunchAgent 重启后面板不会失联（老标签页仍可用）。"""
    env = os.environ.get("SKILLHUB_TOKEN")
    if env:
        return env
    path = os.path.expanduser("~/.skill-hub-token")
    try:
        with open(path, encoding="utf-8") as f:
            t = f.read().strip()
            if t:
                return t
    except OSError:
        pass
    t = secrets.token_urlsafe(18)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(t)
    except OSError:
        pass
    return t


TOKEN = _token()
PORT = int(os.environ.get("SKILLHUB_PORT", "8778"))
sys.path.insert(0, HERE)


def load_cfg():
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def allowed_roots(cfg):
    """只允许操作配置里这些目录下的东西，别的路径一律拒绝。"""
    import glob
    roots = []
    for r in cfg.get("roots", []):
        for p in glob.glob(os.path.expanduser(r["path"])):
            roots.append(os.path.realpath(p))
    pool = (cfg.get("pool") or {}).get("path")
    if pool:
        roots.append(os.path.realpath(os.path.expanduser(pool)))
    return roots


def is_allowed(path, cfg):
    rp = os.path.realpath(path)
    return any(rp == r or rp.startswith(r + os.sep) for r in allowed_roots(cfg))


def find_symlinks_to(target, cfg):
    """找出所有指向 target 的软链（公共库被共享时，删除要一并摘掉）。"""
    links = []
    tgt = os.path.realpath(target)
    for root in allowed_roots(cfg):
        try:
            for name in os.listdir(root):
                p = os.path.join(root, name)
                if os.path.islink(p) and os.path.realpath(p) == tgt:
                    links.append(p)
        except OSError:
            pass
    return links


def load_index(retries=3):
    """读索引；重扫期间文件可能正被替换，重试几次再判定为空。"""
    fp = os.path.join(OUT, "skills.json")
    for i in range(retries):
        try:
            with open(fp, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            time.sleep(0.35 * (i + 1))
    return []


def archive_skill(name, cfg):
    """把同名 skill 收进公共库：实体只留一份，各 agent 目录换成软链。

    效果：其它 agent 里那份被删掉 → 换成指向公共库的软链，
    调用时拿到的永远是公共库那一份，升级只改一处。
    """
    pool_path = os.path.realpath(os.path.expanduser(
        (cfg.get("pool") or {}).get("path") or "~/.agent-skills"))
    entries = [s for s in load_index()
               if s["name"].lower() == name.lower()
               and not s.get("archived") and not s.get("bundle")]
    if not entries:
        return {"error": f"没找到可归集的 {name}（索引里没有它：可能已删除、在包内，或索引正在重建，稍后重试一次）"}

    # 收集所有落点：索引里的目录 + 任何指向实体的软链
    locs, seen = [], set()
    for s in entries:
        for loc in [s["dir"]] + find_symlinks_to(s["dir"], cfg):
            rp = os.path.realpath(loc)
            if rp not in seen:
                seen.add(rp)
                locs.append(loc)
    locs = [l for l in locs if os.path.lexists(l)]
    if not locs:
        return {"error": f"{name} 的目录都不存在了"}
    # 只处理配置里的目录，避免误动别处
    roots = allowed_roots(cfg)
    safe, skipped = [], []
    for l in locs:
        rp = os.path.realpath(l)
        if any(rp == r or rp.startswith(r + os.sep) for r in roots):
            safe.append(l)
        else:
            skipped.append(l)
    locs = safe
    if not locs:
        return {"error": f"{name} 的副本都在配置范围外，已跳过：{skipped}"}

    # 选实体：已在公共库的优先，否则取最近修改的那份（按文件系统精确时间，不用索引里的日期字符串）
    reals = {os.path.realpath(l): l for l in locs}

    def mtime_of(p):
        try:
            return os.path.getmtime(os.path.join(p, "SKILL.md"))
        except OSError:
            try:
                return os.path.getmtime(p)
            except OSError:
                return 0.0

    def md5_of(p):
        try:
            with open(os.path.join(p, "SKILL.md"), "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except OSError:
            return ""

    hashes = {rp: md5_of(rp) for rp in reals}
    in_pool = [rp for rp in reals if rp.startswith(pool_path + os.sep)]
    if in_pool and len(set(hashes.values())) == 1:
        canonical = in_pool[0]          # 内容一样，公共库那份就是实体
    else:
        canonical = max(reals, key=mtime_of)   # 版本分叉：最新的那份说了算

    target = canonical
    moved_from = None
    if not canonical.startswith(pool_path + os.sep):
        os.makedirs(pool_path, exist_ok=True)
        dest = os.path.join(pool_path, os.path.basename(canonical))
        if os.path.lexists(dest) and os.path.realpath(dest) != canonical:
            # 公共库里已有旧版，且内容不同（版本分叉）→ 旧版进废纸篓，新版顶上去
            to_trash(dest)
        n = 2
        while os.path.lexists(dest):
            dest = os.path.join(pool_path, f"{os.path.basename(canonical)}-{n}")
            n += 1
        shutil.move(canonical, dest)
        moved_from, target = canonical, dest

    # 实体自己不能出现在落点里（大小写别名也是同一个 inode），否则会链成自指死循环
    locs = [l for l in locs if os.path.realpath(l) != os.path.realpath(target)]
    if not locs and not moved_from:
        return {"error": f"{name} 只有一个实体、且已在公共库，无需归集",
                "pool": target, "trashed": [], "linked": []}

    # 逐个落点处理：软链直接改指，实体目录进废纸篓，然后统一软链到公共库
    linked, trashed = [], []
    for loc in locs:
        if not os.path.lexists(loc):
            continue
        if os.path.realpath(loc) == os.path.realpath(target):
            continue
        if os.path.islink(loc):
            os.unlink(loc)
        elif os.path.isdir(loc) and os.path.realpath(loc) != os.path.realpath(target):
            to_trash(loc)
            trashed.append(loc)
        os.symlink(target, loc)
        linked.append(loc)

    # 实体被 mv 走后，原位置也要补软链
    if moved_from and not os.path.lexists(moved_from):
        os.symlink(target, moved_from)
        linked.append(moved_from)

    return {"ok": True, "pool": target, "moved_from": moved_from,
            "trashed": trashed, "linked": linked,
            "agents": sorted({s.get("agent") for s in entries})}


def to_trash(path):
    """移到废纸篓：macOS 走 Finder（可从废纸篓还原），其它平台退到 ~/.Trash。"""
    if sys.platform == "darwin":
        # 路径由 argv 传入，不拼进 AppleScript 源码 —— 目录名带引号也注入不了
        script = ('on run argv\n'
                  '  tell application "Finder" to delete (POSIX file (item 1 of argv))\n'
                  'end run')
        try:
            r = subprocess.run(["osascript", "-e", script, path],
                               capture_output=True, text=True, timeout=8)
            if r.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass   # 自动化授权没点、或被系统卡住 → 退到直接挪，不能卡住请求
    dest = os.path.join(os.path.expanduser("~"), ".Trash", os.path.basename(path))
    base, n = dest, 2
    while os.path.exists(dest):
        dest = f"{base}-{n}"
        n += 1
    shutil.move(path, dest)
    return True


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "SkillHub/1.0"

    def log_message(self, fmt, *args):
        pass  # 安静运行

    # ---------- 工具 ----------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        # 记一条审计日志：谁在什么时候动了什么
        if self.command == "POST":
            try:
                with open(_log_path() or os.devnull, "a", encoding="utf-8") as f:
                    f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] POST {self.path} → {code} "
                            f"{json.dumps(obj, ensure_ascii=False, default=str)[:300]}\n")
            except OSError:
                pass
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        return self.headers.get("X-Hub-Token") == TOKEN

    def _host_ok(self):
        """只认本机 Host。恶意域名重绑到 127.0.0.1 后，同源 JS 能读到页面里注入的
        token，所以 Host 不对一律拒掉（DNS rebinding 的标准挡法）。"""
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):          # [::1]:8778 → [::1]
            host = host.split("]")[0] + "]"
        elif host.count(":") == 1:        # 127.0.0.1:8778 → 127.0.0.1
            host = host.split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]", "::1", "")

    def _origin_ok(self):
        """跨站 POST 带 Origin，必须是本机来源；同源请求不带 Origin，放行。"""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return bool(re.match(r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", origin))

    # ---------- GET ----------
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if not self._host_ok():
            self._json({"error": "bad host"}, 403)
            return

        if u.path in ("/", "/index.html"):
            try:
                with open(INDEX, encoding="utf-8") as f:
                    html = f.read()
            except FileNotFoundError:
                self._json({"error": "还没生成仪表盘，先跑 python3 skillhub.py"}, 500)
                return
            html = html.replace('/*__TOKEN__*/""', json.dumps(TOKEN))
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if u.path == "/api/reveal":
            if not self._authed():
                self._json({"error": "unauthorized"}, 403)
                return
            target = (qs.get("path") or [""])[0]
            cfg = load_cfg()
            if not target or not os.path.exists(target) or not is_allowed(target, cfg):
                self._json({"error": "路径不存在或不在允许范围内"}, 400)
                return
            # 文件 → 直接选中它；目录 → 打开该目录
            open_external(target, reveal=os.path.isfile(target))
            self._json({"ok": True, "revealed": target})
            return

        if u.path == "/api/health":
            self._json({"ok": True, "app": "skillhub", "pid": os.getpid()})
            return

        if u.path == "/api/data":
            if not self._authed():
                self._json({"error": "unauthorized"}, 403)
                return
            fp = os.path.join(HERE, "output", "data.json")
            if not os.path.exists(fp):
                return self._json({"error": "索引还没生成，先跑一次 skillhub.py"}, 404)
            with open(fp, encoding="utf-8") as f:
                body = f.read()
            b = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)
            return

        if u.path == "/api/status":
            self._json({"ok": True, "token_required": True})
            return

        self._json({"error": "not found"}, 404)

    # ---------- POST ----------
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not self._host_ok():          # DNS rebinding：Host 必须是本机
            self._json({"error": "bad host"}, 403)
            return
        if not self._authed():
            self._json({"error": "unauthorized"}, 403)
            return
        if not self._origin_ok():        # 跨站表单/JS 不能借浏览器动手
            self._json({"error": "bad origin"}, 403)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "bad json"}, 400)
            return

        if u.path == "/api/delete":
            target = (payload.get("path") or "").strip()
            cfg = load_cfg()
            if not target or not os.path.exists(target):
                self._json({"error": "路径不存在"}, 400)
                return
            if not is_allowed(target, cfg):
                self._json({"error": "拒绝：不在配置的 skill 目录范围内"}, 403)
                return
            links = find_symlinks_to(target, cfg)
            removed = []
            for l in links:
                try:
                    os.unlink(l)
                    removed.append(l)
                except OSError:
                    pass
            try:
                to_trash(target)
            except OSError as e:
                self._json({"error": f"删除失败: {e}"}, 500)
                return
            self._json({"ok": True, "trashed": target, "unlinked": removed,
                        "next": "正在后台重建索引"})
            threading.Thread(target=rescan, daemon=True).start()
            return

        if u.path == "/api/origin":
            name = (payload.get("name") or "").strip()
            if not name:
                self._json({"error": "缺少 name"}, 400)
                return
            import time as _t
            from skillhub import load_origins as _lo, save_origins as _so
            origins = _lo()
            kind = (payload.get("kind") or "self").strip()
            mark = bool(payload.get("mark", True))
            if kind == "blogger":
                store = origins.setdefault("blogger", {})
                val = (payload.get("value") or "").strip() or "推荐"
                if mark:
                    store[name] = {"kind": val, "at": _t.strftime("%Y-%m-%d")}
                else:
                    for k in [k for k in store if k.lower() == name.lower()]:
                        store.pop(k, None)
            else:
                store = origins.setdefault("marks", {})
                if mark:
                    agent = (payload.get("agent") or "").strip() or "未知"
                    store[name] = {"agent": agent, "at": _t.strftime("%Y-%m-%d")}
                else:
                    for k in [k for k in store if k.lower() == name.lower()]:
                        store.pop(k, None)
            _so(origins)
            self._json({"ok": True, "kind": kind,
                        "self": len(origins.get("marks") or {}),
                        "blogger": len(origins.get("blogger") or {})})
            threading.Thread(target=rescan, daemon=True).start()
            return

        if u.path == "/api/archive":
            name = (payload.get("name") or "").strip()
            if not name:
                self._json({"error": "缺少 name"}, 400)
                return
            try:
                res = archive_skill(name, load_cfg())
            except OSError as e:
                self._json({"error": f"归集失败: {e}"}, 500)
                return
            if "error" in res:
                self._json(res, 400)
                return
            self._json(res)
            threading.Thread(target=rescan, daemon=True).start()
            return

        if u.path == "/api/rescan":
            threading.Thread(target=rescan, daemon=True).start()
            self._json({"ok": True, "started": True})
            return

        self._json({"error": "not found"}, 404)


def rescan():
    """后台重建索引与仪表盘（不调模型，复用已有备注）。"""
    r = subprocess.run([sys.executable, os.path.join(HERE, "skillhub.py"), "--no-llm"],
                       cwd=HERE, capture_output=True, text=True)
    with open(_log_path() or os.devnull, "a", encoding="utf-8") as f:
        f.write(f"\n[rescan {time.strftime('%H:%M:%S')}] rc={r.returncode}\n")
        f.write((r.stdout or "")[-2000:])
        if r.returncode != 0:
            f.write((r.stderr or "")[-2000:])


def main():
    if not os.path.exists(INDEX):
        print("还没生成仪表盘，先跑：python3 skillhub.py --no-llm")
        sys.exit(1)
    # 端口可能被别的服务占用（macOS 的 sharingd 就爱占 8770），自动顺延
    httpd = None
    for offset in range(30):
        port_try = PORT + offset
        try:
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port_try), Handler)
            break
        except OSError:
            # 如果占着它的是我们自己的面板，就直接开浏览器，不再起第二个
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port_try}/api/health", timeout=1) as r:
                    if json.loads(r.read().decode()).get("app") == "skillhub":
                        url = f"http://127.0.0.1:{port_try}/"
                        print(f"面板已经在运行: {url}（直接打开，没有重复启动）")
                        open_external(url)
                        sys.exit(0)
            except Exception:
                pass
            continue
    if httpd is None:
        print(f"端口 {PORT}-{PORT + 29} 全被占用，换个端口：SKILLHUB_PORT=9000 python3 serve.py")
        sys.exit(1)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    with httpd:
        print(f"SkillHub 面板已启动: {url}")
        print("  · 在 Finder 中显示 / 移到废纸篓 现在可用")
        print("  · Ctrl-C 停止")
        if not os.environ.get("SKILLHUB_NO_OPEN"):   # 测试 / CI 不该弹浏览器
            threading.Timer(0.6, lambda: open_external(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")


if __name__ == "__main__":
    main()
