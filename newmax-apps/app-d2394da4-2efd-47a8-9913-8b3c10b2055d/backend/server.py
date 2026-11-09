#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件日报 · 本地索引服务

仅用 Python 标准库。只监听 127.0.0.1，所有接口（含 /health）要求
Authorization: Bearer <token>。启动时获取数据目录单例锁，并原子发布
connection.json = {appId, pid, baseUrl, token} 供宿主与新maxApp.files 读取。

职责：扫描监控目录 → 按“创建=新建 / 修改=修改”构建按日期的文件索引
→ 提供 overview / day / config / scan / reveal / stop API。
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

APP_ID_DEFAULT = "app-d2394da4-2efd-47a8-9913-8b3c10b2055d"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_BODY = 1024 * 1024
SCAN_TIMEOUT = 300.0  # 单次扫描最长秒数

HOME = Path.home()

# ---------- 默认监控目录与归属规则（Windows 常见位置，存在才启用） ----------

DEFAULT_ROOTS = [
    {"path": str(HOME / "Downloads"), "label": "下载"},
    {"path": str(HOME / "Documents"), "label": "文档"},
    {"path": str(HOME / "Desktop"), "label": "桌面"},
]

DEFAULT_RULES = [
    {"path": str(HOME / "Documents" / "WeChat Files"), "software": "微信"},
    {"path": str(HOME / "Documents" / "xwechat_files"), "software": "微信"},
    {"path": str(HOME / "xwechat_files"), "software": "微信"},
    {"path": str(HOME / "Library" / "Containers"), "software": "微信"},  # mac 微信容器（非 Windows 时也可能存在）
    {"path": str(HOME / "Documents" / "Tencent Files"), "software": "QQ"},
    {"path": str(HOME / "AppData" / "Roaming" / "Tencent"), "software": "QQ"},
    {"path": str(HOME / "Documents" / "DingTalk"), "software": "钉钉"},
    {"path": str(HOME / "AppData" / "Roaming" / "DingTalk"), "software": "钉钉"},
    {"path": str(HOME / "AppData" / "Roaming" / "dingtalk"), "software": "钉钉"},
    {"path": str(HOME / "Documents" / "Feishu"), "software": "飞书"},
    {"path": str(HOME / "AppData" / "Roaming" / "LarkShell"), "software": "飞书"},
    {"path": str(HOME / "AppData" / "Roaming" / "Lark"), "software": "飞书"},
    {"path": str(HOME / "WPS Drive"), "software": "WPS"},
    {"path": str(HOME / "Documents" / "WXWork"), "software": "企业微信"},
    {"path": str(HOME / "AppData" / "Roaming" / "Tencent" / "WeChat Work"), "software": "企业微信"},
]

DEFAULT_SKIP_DIRS = [
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "$recycle.bin", "system volume information", ".tmp",
    "appdata", ".config",
]

CATEGORIES = [
    ("文档", {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md",
              "csv", "wps", "et", "dps", "ofd", "rtf", "odt", "ods"}),
    ("图片", {"png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "heic",
              "tiff", "tif", "ico", "raw", "psd"}),
    ("视频", {"mp4", "avi", "mkv", "mov", "wmv", "flv", "webm", "mpeg", "ts"}),
    ("音频", {"mp3", "wav", "flac", "aac", "m4a", "ogg", "wma"}),
    ("压缩包", {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "zst"}),
    ("代码数据", {"js", "ts", "py", "java", "c", "cpp", "h", "json", "html",
                 "css", "xml", "sql", "sh", "bat", "ps1", "log", "ini", "yaml", "yml"}),
    ("安装包", {"exe", "msi", "apk", "dmg", "iso"}),
]

TYPE_OTHER = "其他"


def classify(ext: str) -> str:
    e = ext.lower().lstrip(".")
    for name, s in CATEGORIES:
        if e in s:
            return name
    return TYPE_OTHER


def norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def human_size(n: int) -> str:  # 前端自己格式化；后端只给字节
    return str(n)


# ============================================================ 状态与索引

class State:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.settings_path = data_dir / "settings.json"
        self.index_path = data_dir / "index.json"
        self.lock_path = data_dir / "service.lock"
        self.connection_path = data_dir / "connection.json"

        self.files = {}            # normpath -> [name, size, ctime, mtime, software, root_label]
        self.watermark = 0.0       # 上次增量扫描水位（秒）
        self.last_scan = None      # iso 时间
        self.scan_error = None
        self.settings = None       # dict
        self.token = secrets.token_hex(16)

        self.scan_lock = threading.Lock()
        self.scan = {"running": False, "mode": None, "phase": "idle",
                    "dirs_done": 0, "files_seen": 0, "collected": 0,
                    "started_at": None, "error": None}

    # ---- 设置 ----
    def load_settings(self):
        if self.settings_path.exists():
            try:
                self.settings = json.loads(self.settings_path.read_text("utf-8"))
            except Exception:
                self.settings = None
        if not isinstance(self.settings, dict):
            self.settings = {
                "roots": list(DEFAULT_ROOTS),
                "rules": list(DEFAULT_RULES),
                "skipDirs": list(DEFAULT_SKIP_DIRS),
            }
        self.settings.setdefault("roots", list(DEFAULT_ROOTS))
        self.settings.setdefault("rules", list(DEFAULT_RULES))
        self.settings.setdefault("skipDirs", list(DEFAULT_SKIP_DIRS))

    def save_settings(self):
        self._atomic_write(self.settings_path, json.dumps(self.settings, ensure_ascii=False, indent=1))

    # ---- 索引 ----
    def load_index(self):
        if self.index_path.exists():
            try:
                raw = json.loads(self.index_path.read_text("utf-8"))
                self.files = raw.get("files", {})
                self.watermark = float(raw.get("watermark", 0) or 0)
                self.last_scan = raw.get("lastScan")
                self.scan_error = raw.get("scanError")
            except Exception:
                self.files, self.watermark = {}, 0.0

    def save_index(self):
        payload = {"version": 1, "watermark": self.watermark,
                   "lastScan": self.last_scan, "scanError": self.scan_error,
                   "files": self.files}
        self._atomic_write(self.index_path, json.dumps(payload, ensure_ascii=False))

    def _atomic_write(self, path: Path, text: str):
        tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
        tmp.write_text(text, "utf-8")
        os.replace(tmp, path)


ST = State(Path(os.environ.get("NEWMAX_APP_DATA_DIR", ".")))


# ============================================================ 归属计算

def active_roots():
    out = []
    for r in ST.settings["roots"]:
        p = Path(r["path"])
        out.append({"path": str(p), "np": norm(str(p)), "label": r.get("label") or p.name, "exists": p.is_dir()})
    return out


def active_rules():
    rules = []
    for r in ST.settings["rules"]:
        p = str(r["path"])
        rules.append({"path": p, "np": norm(p), "software": r.get("software") or Path(p).name})
    rules.sort(key=lambda x: -len(x["np"]))
    return rules


def attribute(path_norm: str, roots, rules):
    for ru in rules:
        if path_norm == ru["np"] or path_norm.startswith(ru["np"] + os.sep):
            return ru["software"]
    best = None
    for ro in roots:
        if path_norm == ro["np"] or path_norm.startswith(ro["np"] + os.sep):
            if best is None or len(ro["np"]) > len(best["np"]):
                best = ro
    return best["label"] if best else "其他位置"


# ============================================================ 扫描

def walk_and_collect(mode: str):
    roots = active_roots()
    rules = active_rules()
    skip = {s.lower() for s in ST.settings["skipDirs"]}
    wm = ST.watermark if mode == "incremental" else 0.0
    margin = 5.0
    collected = {}
    dirs_done = files_seen = 0
    t0 = time.time()
    err = None

    for root in roots:
        if not root["exists"]:
            continue
        root_p = root["path"]
        for dirpath, dirnames, filenames in os.walk(root_p, topdown=True, followlinks=False,
                                                    onerror=lambda e: None):
            if time.time() - t0 > SCAN_TIMEOUT:
                err = "扫描超时（超过 %d 秒），已收集的部分仍会入库。建议缩小监控目录或在设置中排除大目录。" % int(SCAN_TIMEOUT)
                break
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in skip and not d.startswith(".")]
            try:
                d_mtime = os.stat(dirpath).st_mtime
            except OSError:
                d_mtime = 0
            # 目录自身 mtime 早于水位 → 目录内容近期无增删改，跳过文件收集（继续下探子目录）
            if mode == "incremental" and d_mtime < wm - margin:
                dirs_done += 1
                continue
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                try:
                    fs = os.stat(full)
                except OSError:
                    continue
                if mode == "incremental" and fs.st_ctime < wm - margin and fs.st_mtime < wm - margin:
                    continue
                files_seen += 1
                software = attribute(norm(full), roots, rules)
                collected[norm(full)] = [fn, fs.st_size, fs.st_ctime, fs.st_mtime,
                                         software, root["label"], full]
            dirs_done += 1
            ST.scan["dirs_done"] = dirs_done
            ST.scan["files_seen"] = files_seen
            ST.scan["collected"] = len(collected)
        if err:
            break

    return collected, err, files_seen, dirs_done


def prune_existing():
    """增量后校验旧记录：文件没了就删；还在则刷新 size/mtime。"""
    removed = 0
    for key in list(ST.files.keys()):
        # key 是 normcase 后的路径，Windows 下可用作打开路径；mac/linux 保留原样
        p = key
        try:
            fs = os.stat(p)
            rec = ST.files[key]
            rec[1] = fs.st_size
            rec[3] = fs.st_mtime
            if fs.st_ctime:
                rec[2] = fs.st_ctime
        except OSError:
            ST.files.pop(key, None)
            removed += 1
    return removed


def run_scan(mode: str):
    with ST.scan_lock:
        if ST.scan["running"]:
            return False
        ST.scan.update(running=True, mode=mode, phase="scanning", dirs_done=0,
                       files_seen=0, collected=0, error=None,
                       started_at=datetime.now().isoformat(timespec="seconds"))
        try:
            collected, err, files_seen, dirs_done = walk_and_collect(mode)
            ST.scan["phase"] = "merging"
            if mode == "full":
                ST.files = collected
            else:
                prune_existing()
                ST.files.update(collected)
            wm_new = max(time.time(),
                         max((r[2] for r in ST.files.values()), default=0),
                         max((r[3] for r in ST.files.values()), default=0))
            ST.watermark = wm_new
            ST.last_scan = datetime.now().isoformat(timespec="seconds")
            ST.scan_error = err
            ST.save_index()
            ST.scan.update(phase="done" if not err else "done_with_error",
                           error=err, files_seen=files_seen,
                           dirs_done=dirs_done, collected=len(collected))
        except Exception as e:
            ST.scan.update(phase="failed", error=str(e))
            ST.scan_error = str(e)
        finally:
            ST.scan["running"] = False
        return True


# ============================================================ 视图聚合

def day_created(day: str):
    return [r for r in ST.files.values() if day_of(r[2]) == day]


def day_modified(day: str):
    out = []
    for r in ST.files.values():
        if day_of(r[3]) == day and day_of(r[2]) != day:
            out.append(r)
    return out


def rec_entry(r):
    name, size, ctime, mtime, software, root_label, full = r[:7]
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    return {"name": name, "ext": ext or "", "cat": classify(ext),
            "size": size, "ctime": ctime, "mtime": mtime,
            "software": software, "root": root_label, "path": full}


def group_files(entries, by):
    groups = {}
    for e in entries:
        k = e["software"] if by == "software" else e["cat"]
        groups.setdefault(k, []).append(e)
    out = []
    for k, v in sorted(groups.items(), key=lambda kv: -sum(x["size"] for x in kv[1])):
        v.sort(key=lambda x: -x["mtime"])
        out.append({"key": k, "count": len(v), "bytes": sum(x["size"] for x in v), "files": v})
    return out


def build_overview(days_back=45):
    by_day = {}
    for key, r in ST.files.items():
        name, size, ctime, mtime = r[0], r[1], r[2], r[3]
        d = day_of(ctime)
        slot = by_day.setdefault(d, {"date": d, "n": 0, "b": 0, "sw": {}, "mod": 0})
        slot["n"] += 1
        slot["b"] += size
        sw = r[4] if len(r) > 4 else "其他位置"
        slot["sw"][sw] = slot["sw"].get(sw, 0) + 1
        dm = day_of(mtime)
        if dm != d:
            m = by_day.setdefault(dm, {"date": dm, "n": 0, "b": 0, "sw": {}, "mod": 0})
            m["mod"] += 1
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = datetime.now().timestamp() - days_back * 86400
    tl = [v for k, v in sorted(by_day.items(), reverse=True)
          if datetime.strptime(k, "%Y-%m-%d").timestamp() >= cutoff][:days_back]
    roots = active_roots()
    rules = active_rules()
    return {
        "totals": {"files": len(ST.files), "bytes": sum(r[1] for r in ST.files.values())},
        "lastScan": ST.last_scan, "watermark": ST.watermark, "scanError": ST.scan_error,
        "hasIndex": bool(ST.files),
        "today": today,
        "roots": [{"path": r["path"], "label": r["label"], "exists": r["exists"]} for r in roots],
        "rules": [{"path": ru["path"], "software": ru["software"]} for ru in rules],
        "timeline": tl,
    }


# ============================================================ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "FileDailyIndex/1.0"

    # ---- 基础 ----
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        got = self.headers.get("Authorization", "")
        return got == "Bearer " + ST.token

    def log_message(self, *a):
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("body too large")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            raise ValueError("invalid json body")

    # ---- 路由 ----
    def do_GET(self):
        if not self._authorized():
            return self._json(401, {"ok": False, "error": "unauthorized"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/health":
                return self._json(200, {"ok": True, "data": {
                    "appId": os.environ.get("NEWMAX_APP_ID", APP_ID_DEFAULT),
                    "pid": os.getpid()}})
            if u.path == "/api/overview":
                return self._json(200, {"ok": True, "data": build_overview(int((q.get("days") or ["45"])[0]))})
            if u.path == "/api/day":
                day = (q.get("date") or [""])[0]
                if not DATE_RE.match(day):
                    return self._json(400, {"ok": False, "error": "date 需为 YYYY-MM-DD"})
                created = [rec_entry(r) for r in ST.files.values() if day_of(r[2]) == day]
                modified = [rec_entry(r) for r in ST.files.values()
                            if day_of(r[3]) == day and day_of(r[2]) != day]
                data = {
                    "date": day,
                    "created": {"count": len(created), "bytes": sum(e["size"] for e in created),
                                 "bySoftware": group_files(created, "software"),
                                 "byType": group_files(created, "type")},
                    "modified": {"count": len(modified), "bytes": sum(e["size"] for e in modified),
                                  "bySoftware": group_files(modified, "software"),
                                  "byType": group_files(modified, "type")},
                    "lastScan": ST.last_scan, "hasIndex": bool(ST.files),
                }
                return self._json(200, {"ok": True, "data": data})
            if u.path == "/api/config":
                return self._json(200, {"ok": True, "data": {
                    "roots": ST.settings["roots"], "rules": ST.settings["rules"],
                    "skipDirs": ST.settings["skipDirs"]}})
            if u.path == "/api/scan/status":
                return self._json(200, {"ok": True, "data": dict(ST.scan)})
            return self._json(404, {"ok": False, "error": "not found"})
        except Exception as e:
            return self._json(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        if not self._authorized():
            return self._json(401, {"ok": False, "error": "unauthorized"})
        u = urlparse(self.path)
        try:
            body = self._body()
        except ValueError as e:
            return self._json(400, {"ok": False, "error": str(e)})
        try:
            if u.path == "/api/scan":
                mode = body.get("mode", "incremental")
                if mode not in ("full", "incremental"):
                    return self._json(400, {"ok": False, "error": "mode 需为 full|incremental"})
                if ST.scan["running"]:
                    return self._json(409, {"ok": False, "error": "扫描正在进行中"})
                threading.Thread(target=run_scan, args=(mode,), daemon=True).start()
                return self._json(200, {"ok": True, "data": {"started": True, "mode": mode}})
            if u.path == "/api/config":
                roots = body.get("roots")
                rules = body.get("rules")
                skips = body.get("skipDirs")
                if not isinstance(roots, list) or not roots:
                    return self._json(400, {"ok": False, "error": "roots 不能为空"})
                clean_roots = []
                seen_np = set()
                for r in roots:
                    p = str(r.get("path", "")).strip().strip('"')
                    if not p:
                        continue
                    np_ = norm(p)
                    if np_ in seen_np:
                        continue
                    if not Path(p).is_dir():
                        return self._json(400, {"ok": False, "error": f"目录不存在：{p}"})
                    seen_np.add(np_)
                    label = str(r.get("label", "")).strip() or Path(p).name
                    clean_roots.append({"path": p, "label": label})
                if not clean_roots:
                    return self._json(400, {"ok": False, "error": "没有有效目录"})
                clean_rules = []
                for r in (rules or []):
                    p = str(r.get("path", "")).strip().strip('"')
                    sw = str(r.get("software", "")).strip()
                    if not p or not sw:
                        continue
                    clean_rules.append({"path": p, "software": sw})
                clean_skip = [str(s).strip().lower() for s in (skips or ST.settings["skipDirs"]) if str(s).strip()]
                ST.settings = {"roots": clean_roots, "rules": clean_rules, "skipDirs": clean_skip or list(DEFAULT_SKIP_DIRS)}
                ST.save_settings()
                return self._json(200, {"ok": True, "data": ST.settings})
            if u.path == "/api/reveal":
                target = str(body.get("path", ""))
                tn = norm(target)
                roots, rules = active_roots(), active_rules()
                allowed = any(tn == r["np"] or tn.startswith(r["np"] + os.sep) for r in roots + rules)
                if not allowed:
                    return self._json(403, {"ok": False, "error": "该路径不在监控范围内"})
                if not os.path.exists(tn):
                    return self._json(404, {"ok": False, "error": "文件已不存在"})
                if sys.platform == "win32":
                    subprocess.Popen(["explorer.exe", f"/select,{tn}"])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", "-R", tn])
                else:
                    subprocess.Popen(["xdg-open", os.path.dirname(tn)])
                return self._json(200, {"ok": True, "data": {"opened": True}})
            if u.path == "/api/stop":
                self._json(200, {"ok": True, "data": {"stopping": True}})
                def _hard_exit():
                    time.sleep(0.4)
                    try:
                        ST.lock_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    try:
                        ST.connection_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    os._exit(0)
                threading.Thread(target=_hard_exit, daemon=False).start()
                return
            return self._json(404, {"ok": False, "error": "not found"})
        except Exception as e:
            return self._json(500, {"ok": False, "error": str(e)})


# ============================================================ 启动

def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_singleton_lock():
    if ST.lock_path.exists():
        holder = None
        try:
            holder = int(ST.lock_path.read_text("utf-8").strip())
        except Exception:
            holder = None
        if holder and pid_alive(holder):
            print(f"另一实例正在运行 (pid={holder})，本进程退出", flush=True)
            sys.exit(0)
        # 陈旧锁：属主已死，接管
        try:
            ST.lock_path.unlink(missing_ok=True)
        except Exception:
            pass
    try:
        fd = os.open(str(ST.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        print("锁竞争失败，本进程退出", flush=True)
        sys.exit(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.environ.get("NEWMAX_APP_DATA_DIR", "."))
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    ST.data_dir = data_dir
    ST.settings_path = data_dir / "settings.json"
    ST.index_path = data_dir / "index.json"
    ST.lock_path = data_dir / "service.lock"
    ST.connection_path = data_dir / "connection.json"

    acquire_singleton_lock()
    ST.load_settings()
    ST.load_index()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    payload = {"appId": os.environ.get("NEWMAX_APP_ID", APP_ID_DEFAULT),
               "pid": os.getpid(), "baseUrl": base, "token": ST.token}
    ST._atomic_write(ST.connection_path, json.dumps(payload))
    print(f"ready {base}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ST.lock_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
