# -*- coding: utf-8 -*-
"""
任务台账（观测层 · 落地九命中枢"token 统计/可问责"设计）
=========================================================
每单任务一行 NDJSON：谁的哪个项目、什么意图/档位、跑了多久、
token 用量与花费（来自 claude --output-format json 的 usage）、成败。
观测看板 observe/board.py 直接读这个文件渲染。
"""
import os, json, datetime, threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE_DIR = os.path.join(ROOT, "_state")
os.makedirs(STATE_DIR, exist_ok=True)
LEDGER_FILE = os.path.join(STATE_DIR, "ledger.jsonl")

_lock = threading.Lock()


def record(**kw):
    """落一行台账。约定字段：node/intent/tier/perm_mode/duration_s/ok/rc/
    tokens_in/tokens_out/cost_usd/task/mid。缺省容忍。"""
    row = {"ts": datetime.datetime.now().isoformat(timespec="seconds")}
    row.update(kw)
    if "task" in row:
        row["task"] = str(row["task"])[:100]
    try:
        with _lock:
            with open(LEDGER_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return row


def _tail_lines(path, n):
    """字节级尾读：只从文件末尾按块回退读取，直到凑够 n 行或到文件头。
    台账增长后 recent() 不再整文件进内存（看板每 15s 刷一次）。
    换行符 0x0A 不会出现在 UTF-8 多字节序列中间，按 b"\\n" 切分安全；
    首块可能截断的半行只在"未读到文件头"时存在，且必被 [-n:] 裁掉。"""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        data = b""
        while pos > 0 and data.count(b"\n") <= n:
            step = min(8192, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":   # 文件以 \n 结尾时去掉末尾空段
        lines.pop()
    return [ln.decode("utf-8", errors="replace") for ln in lines[-n:]]


def recent(n=50):
    """最近 n 条（新在前）。坏行跳过；与旧全量 readlines 实现结果一致，但只尾读。"""
    try:
        lines = _tail_lines(LEDGER_FILE, n)
    except Exception:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return list(reversed(out))


def summary():
    """全量汇总：按节点计数 + 按模型成本 + token/花费总量。"""
    by_node, by_model = {}, {}
    total = {"tasks": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "fail": 0,
             "cost_unknown": 0}
    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                total["tasks"] += 1
                total["tokens_in"] += int(r.get("tokens_in") or 0)
                total["tokens_out"] += int(r.get("tokens_out") or 0)
                total["cost_usd"] += float(r.get("cost_usd") or 0)
                if r.get("ok") is False:
                    total["fail"] += 1
                if r.get("cost_src") == "none":   # 成本未知：诚实计数，不当 0 混入省钱假象
                    total["cost_unknown"] += 1
                nd = r.get("node") or "?"
                by_node[nd] = by_node.get(nd, 0) + 1
                md = r.get("model") or "(未知)"
                slot = by_model.setdefault(md, {"tasks": 0, "cost_usd": 0.0})
                slot["tasks"] += 1
                slot["cost_usd"] += float(r.get("cost_usd") or 0)
    except Exception:
        pass
    total["cost_usd"] = round(total["cost_usd"], 4)
    for slot in by_model.values():
        slot["cost_usd"] = round(slot["cost_usd"], 4)
    return {"total": total, "by_node": by_node, "by_model": by_model}
