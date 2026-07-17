# -*- coding: utf-8 -*-
"""
Token Broker + 权限分档（移植自九命中枢 dispatcher.py，两点升级）
================================================================
1. 落盘化：token 存 _state/tokens.json —— 进程重启不失效，撑得起"派发层真校验"。
2. 真正被消费：dispatcher 起 claude 前必须 issue + validate，按档位下发 --allowed-tools，
   取代一刀切的 --dangerously-skip-permissions。

权限四档（tier）：
  T0 只读       Read/Grep/Glob —— 自动放行
  T1 项目内写   全工具（Bash 天然是广权限，jail 靠 cwd+prompt+审计） —— 自动放行+留痕
  T2 系统权限   同 T1 工具，但必须先过飞书审批卡（不点不跑）
  T3 危险操作   同 T2，审批卡 + 原文复述确认
每次 issue/validate 失败/revoke/expire 都落 NDJSON 审计（CIAA 可问责）。
"""
import os, json, time, secrets, datetime, threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE_DIR = os.path.join(ROOT, "_state")
os.makedirs(STATE_DIR, exist_ok=True)
TOKENS_FILE = os.path.join(STATE_DIR, "tokens.json")
AUDIT_FILE = os.path.join(STATE_DIR, "token_audit.jsonl")

# 各档位对应下发给 claude 的工具白名单
TIER_TOOLS = {
    0: ["Read", "Grep", "Glob", "LS"],
    1: ["Read", "Grep", "Glob", "LS", "Edit", "Write", "MultiEdit", "NotebookEdit",
        "Bash", "WebFetch", "WebSearch", "TodoWrite", "Task", "Skill", "SlashCommand"],
}
TIER_TOOLS[2] = TIER_TOOLS[1]   # T2/T3 工具面同 T1，区别在"必须先过审批门"
TIER_TOOLS[3] = TIER_TOOLS[1]

TIER_NAME = {0: "T0只读", 1: "T1项目内写", 2: "T2系统权限", 3: "T3危险操作"}

_lock = threading.Lock()


def _now():
    return time.time()


def _load():
    try:
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d):
    tmp = TOKENS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, TOKENS_FILE)


def _audit(action, rec, extra=None):
    try:
        row = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "action": action, "token": rec.get("token"),
               "node": rec.get("node"), "tier": rec.get("tier"),
               "task": (rec.get("task") or "")[:80]}
        if extra:
            row.update(extra)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _budget_for(tier):
    """按档取单次派发预算上限（美元）。缺口4：0=不限。"""
    try:
        from core.registry import settings
        cfg = settings().get("budget_by_tier") or {}
        return float(cfg.get(str(tier), cfg.get(tier, 0)) or 0)
    except Exception:
        return 0.0


def issue(node_id, task, tier, ttl_seconds=1800):
    """为一个任务签发短效权限证。返回 record（含 token / allowed_tools / budget_usd）。"""
    tier = max(0, min(3, int(tier)))
    rec = {
        "token": secrets.token_hex(8),
        "node": node_id, "task": (task or "")[:120], "tier": tier,
        "allowed_tools": list(TIER_TOOLS[tier]),
        "budget_usd": _budget_for(tier),
        "issued_at": _now(), "expire_at": _now() + ttl_seconds,
    }
    with _lock:
        d = _load()
        d[rec["token"]] = rec
        # 顺手清扫过期证
        for t in [k for k, v in d.items() if _now() > v.get("expire_at", 0)]:
            _audit("expire", d.pop(t))
        _save(d)
    _audit("issue", rec)
    return rec


def validate(token, node_id=None, tool=None):
    """派发层执行前校验。返回 (ok, rec_or_reason)。"""
    with _lock:
        d = _load()
        rec = d.get(token)
        if not rec:
            _audit("deny", {"token": token, "node": node_id, "tier": None, "task": ""},
                   {"reason": "未知token"})
            return False, "未知 token（可能已失效）"
        if _now() > rec["expire_at"]:
            _audit("expire", d.pop(token))
            _save(d)
            return False, "token 已过期"
        if node_id and rec["node"] != node_id:
            _audit("deny", rec, {"reason": f"token属于{rec['node']}≠{node_id}"})
            return False, f"token 属于节点「{rec['node']}」，不能用于「{node_id}」"
        if tool and tool not in rec["allowed_tools"]:
            _audit("deny", rec, {"reason": f"工具{tool}越权"})
            return False, f"工具「{tool}」不在 T{rec['tier']} 白名单"
    return True, rec


def revoke(token):
    with _lock:
        d = _load()
        rec = d.pop(token, None)
        if rec:
            _save(d)
            _audit("revoke", rec)
    return rec is not None


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    r = issue("cehua", "自测任务", 1, ttl_seconds=5)
    print("签发:", r["token"], "tier=", r["tier"], "tools=", len(r["allowed_tools"]))
    print("同节点校验:", validate(r["token"], "cehua")[0])
    print("跨节点校验:", validate(r["token"], "touyan"))
    print("越权工具(T0证调Bash):")
    r0 = issue("cehua", "只读任务", 0)
    print(" ", validate(r0["token"], "cehua", tool="Bash"))
    time.sleep(6)
    print("过期校验:", validate(r["token"], "cehua"))
