# -*- coding: utf-8 -*-
"""
分层共享记忆（移植自九命中枢 hub/memory/store.py，接到统一注册表）
==================================================================
4 层：L0 宪法=全局 CLAUDE.md(不入库) / L1 全局技艺 / L2 品类共享 / L3 项目私有。
MD 做真相源(frontmatter 路由元数据) + sqlite 索引；向量(全局 qwen3-embedding)不可用退 FTS5 trigram。
scope 强制：search 与 get(按 ID 直取)都过校验，堵"ID 直取绕过隔离"越权。
晋升 promote 需 approved=True。
猫管家接线点：bridge 派发前用 search(task, node_id) 注入 top-k 相关记忆。
"""
import os, sys, re, json, sqlite3, uuid, math, array, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MEM_DIR = os.path.join(ROOT, "memory")
os.makedirs(MEM_DIR, exist_ok=True)
DB = os.path.join(MEM_DIR, "index.db")

sys.path.insert(0, ROOT)
from core.registry import category_of   # noqa: E402

# 复用全局 embedding 引擎(qwen3-embedding, ollama:11434)，失败则纯 FTS
_EMB = False
try:
    sys.path.insert(0, os.path.expanduser("~/.claude/memory/scripts"))
    from embedding import encode_query, encode_doc   # noqa
    _EMB = True
except Exception:
    _EMB = False

VALID_SCOPE = ("L1", "L2", "L3")
VALID_TYPE = ("knowledge", "pattern", "insight", "episode", "bug")
# 阶段4 记忆暂存闸：active=生效可被派发注入；staging=待人工复核，对 inject/search 不可见(断污染回路)。
VALID_STATUS = ("active", "staging")


def _slug(s, n=36):
    s = re.sub(r"[^\w一-鿿]+", "-", s or "").strip("-")
    return s[:n] or "mem"


def _to_blob(vec):
    return array.array("f", vec).tobytes()


def _from_blob(b):
    a = array.array("f"); a.frombytes(b); return list(a)


def _cos(a, b):
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb + 1e-9)


def scope_dir(scope, category="", project=""):
    if scope == "L1":
        return os.path.join(MEM_DIR, "L1_global")
    if scope == "L2":
        return os.path.join(MEM_DIR, f"L2_{category or 'misc'}")
    return os.path.join(MEM_DIR, f"L3_{project or 'misc'}")


def parse_md(path):
    with open(path, encoding="utf-8") as f:
        txt = f.read()
    meta, body = {}, txt
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", txt, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = m.group(2)
    return meta, body.strip()


class MemoryStore:
    def __init__(self):
        self.conn = sqlite3.connect(DB, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS mem(
          id TEXT PRIMARY KEY, scope TEXT, category TEXT, project TEXT,
          type TEXT, importance REAL DEFAULT 0.6, title TEXT, body TEXT,
          tags TEXT, file TEXT, created TEXT, vec BLOB,
          status TEXT DEFAULT 'active');
        CREATE INDEX IF NOT EXISTS i_scope ON mem(scope);
        CREATE INDEX IF NOT EXISTS i_cat ON mem(category);
        CREATE INDEX IF NOT EXISTS i_proj ON mem(project);
        CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
          id UNINDEXED, title, body, tags, tokenize='trigram');
        """)
        # 阶段4 迁移：旧库无 status 列时补加（默认 active，存量记忆一律生效）。
        # 顺序铁律：必须先 ALTER 加列再建 i_status 索引——反了会对无列旧表建索引直接炸整个 _init。
        try:
            cols = [r[1] for r in self.conn.execute("PRAGMA table_info(mem)").fetchall()]
            if "status" not in cols:
                self.conn.execute("ALTER TABLE mem ADD COLUMN status TEXT DEFAULT 'active'")
            self.conn.execute("CREATE INDEX IF NOT EXISTS i_status ON mem(status)")
        except Exception:
            pass
        self.conn.commit()

    # ---------- 可见性(scope 强制核心) ----------
    def _visible(self, row, project):
        cat = category_of(project)
        if row["scope"] == "L1":
            return True
        if row["scope"] == "L2":
            return row["category"] == cat and bool(cat)
        if row["scope"] == "L3":
            return row["project"] == project
        return False

    def _visible_where(self, project):
        cat = category_of(project)
        return ("(scope='L1' OR (scope='L2' AND category=? AND category!='') "
                "OR (scope='L3' AND project=?))", (cat, project))

    # ---------- 写 ----------
    def add(self, content, scope, type="knowledge", importance=0.6, tags=None,
            project=None, category=None, title=None, mem_id=None, created=None,
            status="active"):
        if scope not in VALID_SCOPE:
            raise ValueError(f"scope 必须是 {VALID_SCOPE}")
        if status not in VALID_STATUS:
            raise ValueError(f"status 必须是 {VALID_STATUS}")
        if scope == "L2" and not category:
            raise ValueError("L2 品类共享必须给 category")
        if scope == "L3" and not project:
            raise ValueError("L3 项目私有必须给 project")
        mem_id = mem_id or "m_" + uuid.uuid4().hex[:10]
        title = title or content.strip().splitlines()[0][:50]
        tags = tags or []
        # BUG-M1 修复：created 可透传（reindex 保留原创建时间，不再集体篡改成当天）；
        # 不传（None/空串）保持旧行为用当前时间，完全向后兼容。
        created = created or datetime.datetime.now().isoformat(timespec="seconds")
        d = scope_dir(scope, category or "", project or "")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{mem_id}_{_slug(title)}.md")
        # BUG-M2 修复：title 变更导致 slug 变化时，清掉目标目录内同 mem_id 的旧 slug 文件，
        # 避免同一 mem_id 双份 MD 在 reindex 时互相覆盖 db 行。前缀带下划线分隔符，
        # 只匹配 "{mem_id}_*.md" 且排除目标文件本身，绝不误删其他 mem_id。
        _prefix = f"{mem_id}_"
        for _fn in os.listdir(d):
            if _fn.startswith(_prefix) and _fn.endswith(".md") \
                    and os.path.join(d, _fn) != path:
                try:
                    os.remove(os.path.join(d, _fn))
                except OSError:
                    pass  # 删不掉（占用/权限）不阻塞写入，新文件仍是权威
        safe_title = title.replace("\n", " ")
        fm = (f"---\nid: {mem_id}\nscope: {scope}\ncategory: {category or ''}\n"
              f"project: {project or ''}\ntype: {type}\nimportance: {importance}\n"
              f"title: {safe_title}\ntags: {','.join(tags)}\ncreated: {created}\n"
              f"status: {status}\n---\n{content.strip()}\n")
        with open(path, "w", encoding="utf-8") as f:
            f.write(fm)
        rel = os.path.relpath(path, MEM_DIR)
        vec_blob = None
        if _EMB:
            try:
                vec_blob = _to_blob(encode_doc(f"{title}\n{content}"))
            except Exception:
                vec_blob = None
        self.conn.execute("DELETE FROM mem WHERE id=?", (mem_id,))
        self.conn.execute("DELETE FROM mem_fts WHERE id=?", (mem_id,))
        self.conn.execute(
            "INSERT INTO mem(id,scope,category,project,type,importance,title,body,tags,file,created,vec,status)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mem_id, scope, category or "", project or "", type, importance, title,
             content.strip(), ",".join(tags), rel, created, vec_blob, status))
        self.conn.execute("INSERT INTO mem_fts(id,title,body,tags) VALUES(?,?,?,?)",
                          (mem_id, title, content.strip(), ",".join(tags)))
        self.conn.commit()
        return mem_id

    # ---------- 读(scope 过滤) ----------
    def _row(self, r):
        try:
            status = r["status"] or "active"
        except (IndexError, KeyError):
            status = "active"
        return {"id": r["id"], "scope": r["scope"], "category": r["category"],
                "project": r["project"], "type": r["type"], "importance": r["importance"],
                "title": r["title"], "body": r["body"], "tags": r["tags"], "file": r["file"],
                "status": status}

    def search(self, query, project, k=6, include_staging=False):
        where, params = self._visible_where(project)
        sql = f"SELECT * FROM mem WHERE {where}"
        if not include_staging:
            # 阶段4 断污染回路：默认只捞 active；staging(reflector 自动沉淀未复核)对派发注入不可见。
            sql += " AND (status='active' OR status IS NULL)"
        rows = self.conn.execute(sql, params).fetchall()
        if not rows:
            return []
        qv = None
        if _EMB:
            try:
                qv = encode_query(query)
            except Exception:
                qv = None
        scored = []
        if qv is not None:
            for r in rows:
                base = _cos(qv, _from_blob(r["vec"])) if r["vec"] else 0.0
                scored.append((base * (0.5 + 0.5 * (r["importance"] or 0.6)), r))
            mode = "vector"
        else:
            try:
                hit = {x[0] for x in self.conn.execute(
                    "SELECT id FROM mem_fts WHERE mem_fts MATCH ?", (f'"{query}"',)).fetchall()}
            except Exception:
                hit = set()
            for r in rows:
                base = 1.0 if r["id"] in hit else 0.0
                scored.append((base * (0.5 + 0.5 * (r["importance"] or 0.6)), r))
            mode = "fts"
        scored.sort(key=lambda x: -x[0])
        return [dict(self._row(r), score=round(sc, 4), _mode=mode) for sc, r in scored[:k] if sc > 0]

    def get(self, mem_id, project):
        """按 ID 直取也必须过 scope 校验 → 堵住越权 bug。"""
        r = self.conn.execute("SELECT * FROM mem WHERE id=?", (mem_id,)).fetchone()
        if not r:
            return None
        if not self._visible(r, project):
            return {"denied": True, "id": mem_id, "scope": r["scope"],
                    "reason": f"记忆 {mem_id}(scope={r['scope']},cat={r['category']},proj={r['project']}) "
                              f"不在项目「{project}」可见范围"}
        return self._row(r)

    # ---------- 调权(反馈回流用) ----------
    def adjust_importance(self, mem_id, delta):
        """按反馈调 importance（钳在 0.1~1.0）。同步改 sqlite 索引 + MD 真相源
        frontmatter（只改 db 会在下次 reindex 时被 MD 原值冲掉）。"""
        r = self.conn.execute("SELECT * FROM mem WHERE id=?", (mem_id,)).fetchone()
        if not r:
            return None
        new_imp = round(min(1.0, max(0.1, float(r["importance"] or 0.6) + delta)), 2)
        self.conn.execute("UPDATE mem SET importance=? WHERE id=?", (new_imp, mem_id))
        self.conn.commit()
        path = os.path.join(MEM_DIR, r["file"])
        try:
            with open(path, encoding="utf-8") as f:
                txt = f.read()
            txt2 = re.sub(r"(?m)^importance:\s*[\d.]+\s*$", f"importance: {new_imp}", txt, count=1)
            if txt2 != txt:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(txt2)
        except Exception:
            pass   # MD 改不动时 db 已生效，reindex 会回退——诚实：调权以 MD 为准
        return new_imp

    # ---------- 晋升(走审批) ----------
    def promote(self, mem_id, to_scope, category=None, approved=False):
        if not approved:
            return {"pending_approval": True, "id": mem_id, "to": to_scope,
                    "hint": "晋升属共享动作,需 approved=True(或人工审批)才执行"}
        r = self.conn.execute("SELECT * FROM mem WHERE id=?", (mem_id,)).fetchone()
        if not r:
            raise ValueError("无此记忆")
        old_path = os.path.join(MEM_DIR, r["file"])
        _, body = parse_md(old_path) if os.path.exists(old_path) else ({}, r["body"])
        new_cat = category or (r["category"] if to_scope == "L2" else "")
        new_proj = r["project"] if to_scope == "L3" else ""
        if to_scope == "L2" and not new_cat:
            raise ValueError("升 L2 需 category")
        if os.path.exists(old_path):
            os.remove(old_path)
        tags = r["tags"].split(",") if r["tags"] else []
        self.add(body, to_scope, type=r["type"], importance=r["importance"], tags=tags,
                 project=new_proj or None, category=new_cat or None, title=r["title"], mem_id=mem_id)
        return {"promoted": mem_id, "from": r["scope"], "to": to_scope, "category": new_cat or None}

    # ---------- 阶段4 暂存闸：晋升/弃用/列举/过期 ----------
    def _set_status_md(self, rel_file, status):
        """同步 MD frontmatter 的 status（只改 db 会在 reindex 时被 MD 冲回）。旧 MD 无 status 行则补插。"""
        path = os.path.join(MEM_DIR, rel_file or "")
        try:
            with open(path, encoding="utf-8") as f:
                txt = f.read()
            if re.search(r"(?m)^status:\s*\w+\s*$", txt):
                txt2 = re.sub(r"(?m)^status:\s*\w+\s*$", f"status: {status}", txt, count=1)
            else:
                txt2 = re.sub(r"(?m)^(created:.*)$", r"\1\nstatus: " + status, txt, count=1)
                if txt2 == txt:                       # 连 created 都没有：插到第一个 --- 后
                    txt2 = txt.replace("---\n", f"---\nstatus: {status}\n", 1)
            if txt2 != txt:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(txt2)
        except Exception:
            pass   # MD 改不动时 db 已生效，reindex 会回退——诚实：以 MD 为准

    def activate(self, mem_id):
        """晋升 staging→active（人工复核通过后生效）。改 db + MD 真相源。返回 True=成功。"""
        r = self.conn.execute("SELECT * FROM mem WHERE id=?", (mem_id,)).fetchone()
        if not r:
            return False
        self.conn.execute("UPDATE mem SET status='active' WHERE id=?", (mem_id,))
        self.conn.commit()
        self._set_status_md(r["file"], "active")
        return True

    def discard(self, mem_id, only_staging=True):
        """弃用记忆：删 db 行 + MD 文件。only_staging=True 时只删 staging（防误删已生效记忆）。"""
        r = self.conn.execute("SELECT * FROM mem WHERE id=?", (mem_id,)).fetchone()
        if not r:
            return False
        cur = (r["status"] if "status" in r.keys() else "active") or "active"
        if only_staging and cur != "staging":
            return False
        self.conn.execute("DELETE FROM mem WHERE id=?", (mem_id,))
        self.conn.execute("DELETE FROM mem_fts WHERE id=?", (mem_id,))
        self.conn.commit()
        try:
            p = os.path.join(MEM_DIR, r["file"] or "")
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
        return True

    def list_staging(self, project=None):
        """列出待复核的 staging 记忆（供日回顾卡/晋升/过期扫描）。project 给了则过滤。"""
        if project:
            rows = self.conn.execute(
                "SELECT * FROM mem WHERE status='staging' AND project=?", (project,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM mem WHERE status='staging'").fetchall()
        return [dict(self._row(r), created=r["created"]) for r in rows]

    def expire_staging(self, days=21):
        """删超过 days 天仍未晋升的 staging（防淤积 + 防污染记忆无限暂存）。返回删除数。"""
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.conn.execute(
            "SELECT id FROM mem WHERE status='staging' AND created < ?", (cutoff,)).fetchall()
        n = 0
        for r in rows:
            if self.discard(r["id"], only_staging=True):
                n += 1
        return n

    def reindex(self):
        """从 MD 真相源重建索引。"""
        self.conn.execute("DELETE FROM mem"); self.conn.execute("DELETE FROM mem_fts"); self.conn.commit()
        n = 0
        for root, _, files in os.walk(MEM_DIR):
            for fn in files:
                if not fn.endswith(".md"):
                    continue
                fp = os.path.join(root, fn)
                if not os.path.exists(fp):
                    continue  # 同 mem_id 旧 slug 文件已被本轮 add() 的 M2 清理删掉
                meta, body = parse_md(fp)
                if not meta.get("id"):
                    continue
                self.add(body, meta.get("scope", "L3"), type=meta.get("type", "knowledge"),
                         importance=float(meta.get("importance") or 0.6),
                         tags=[t for t in meta.get("tags", "").split(",") if t],
                         project=meta.get("project") or None, category=meta.get("category") or None,
                         title=meta.get("title"), mem_id=meta.get("id"),
                         created=meta.get("created"),          # BUG-M1：透传原 created，不篡改时间线
                         status=meta.get("status") or "active")  # 阶段4：透传 status，防 reindex 把 staging 冲成 active
                n += 1
        return n

    def stats(self):
        out = {}
        for r in self.conn.execute("SELECT scope, COUNT(*) c FROM mem GROUP BY scope"):
            out[r["scope"]] = r["c"]
        return {"by_scope": out, "embedding": _EMB, "total": sum(out.values())}


_STORE = {"obj": None}


def _shared_store():
    """复用单个 MemoryStore（连接 check_same_thread=False，ws 多线程安全）。
    原先每条消息都 new 一个 → 每次重开 sqlite + 跑一遍建表/ALTER/建索引 DDL，纯浪费。"""
    if _STORE["obj"] is None:
        _STORE["obj"] = MemoryStore()
    return _STORE["obj"]


def inject_context(task_text, node_id, k=3, max_chars=400):
    """派发前记忆注入：检索该节点可见的相关记忆，拼成上下文块。查不到返回空串。"""
    try:
        ms = _shared_store()
        hits = ms.search(task_text, node_id, k=k)
        if not hits:
            return ""
        lines = [f"【项目相关记忆（猫管家分层记忆库，{len(hits)} 条，供参考避坑）】"]
        for h in hits:
            body = (h["body"] or "").replace("\n", " ")[:max_chars]
            lines.append(f"- [{h['scope']}] {h['title']}：{body}")
        return "\n".join(lines)
    except Exception:
        return ""


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("add"); a.add_argument("content")
    a.add_argument("--scope", required=True); a.add_argument("--type", default="knowledge")
    a.add_argument("--importance", type=float, default=0.6); a.add_argument("--tags", default="")
    a.add_argument("--project", default=None); a.add_argument("--category", default=None)
    a.add_argument("--title", default=None)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--project", required=True); s.add_argument("-k", type=int, default=6)
    g = sub.add_parser("get"); g.add_argument("id"); g.add_argument("--project", required=True)
    sub.add_parser("stats"); sub.add_parser("reindex")
    args = ap.parse_args()
    ms = MemoryStore()
    if args.cmd == "add":
        print("已存", ms.add(args.content, args.scope, type=args.type, importance=args.importance,
                             tags=[t for t in args.tags.split(",") if t], project=args.project,
                             category=args.category, title=args.title))
    elif args.cmd == "search":
        for r in ms.search(args.query, args.project, k=args.k):
            print(f"  [{r['scope']}] {r['title']}  (score={r['score']}, {r['_mode']})")
    elif args.cmd == "get":
        print(json.dumps(ms.get(args.id, args.project), ensure_ascii=False, indent=2))
    elif args.cmd == "stats":
        print(json.dumps(ms.stats(), ensure_ascii=False, indent=2))
    elif args.cmd == "reindex":
        print(f"已从 MD 重建索引: {ms.reindex()} 条")
    else:
        ap.print_help()
