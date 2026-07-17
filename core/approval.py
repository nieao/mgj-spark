# -*- coding: utf-8 -*-
"""
审批门（猫管家新增 · 把"确认卡是装饰"升级为"不点不跑"）
========================================================
T2/T3 任务不直接执行：先落 _state/pending_approval.json，发审批卡，
用户点 ✅执行（或文字"批准"）才真正派发；点 ❌取消（或"取消"）作废。
待审任务 30 分钟过期自动作废（防止陈年任务被误放行）。
"""
import os, json, time, uuid, threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE_DIR = os.path.join(ROOT, "_state")
os.makedirs(STATE_DIR, exist_ok=True)
PENDING_FILE = os.path.join(STATE_DIR, "pending_approval.json")
TTL = 1800  # 30 分钟过期（对齐文档承诺与飞书卡片"30分钟自动作废"文案、bridge PENDING_WINDOW 口径）

_lock = threading.Lock()


def _load():
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(items):
    tmp = PENDING_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PENDING_FILE)


def _fresh(items):
    now = time.time()
    return [it for it in items if now - it.get("ts", 0) <= TTL]


def add(text, node_ids, tier, intent, extra="", mode="pipeline", initiator=None):
    """登记一个待审任务，返回 pid。mode 存协同模式（parallel/pipeline/single），批准后按它跑。
    initiator：发起本次任务的人（open_id），供 take() 做 operator 校验（纵深防御，可选）。"""
    pid = "ap_" + uuid.uuid4().hex[:8]
    with _lock:
        items = _fresh(_load())
        items.append({"pid": pid, "text": text, "nodes": node_ids, "tier": tier,
                      "intent": intent, "extra": extra, "mode": mode, "ts": time.time(),
                      "initiator": initiator})
        _save(items[-20:])
    return pid


def take(pid=None, operator=None):
    """取出并移除一个待审任务：给 pid 取指定的；不给取最新一条。没有返回 None。
    operator：本次批准/取消操作者的 open_id。若提供且与创建时记录的 initiator 不一致，
    拒绝放行（记录不移除，返回 None 并告警）——防 pending 被跨会话/伪造回调批准的纵深防御。
    operator=None 或该记录没有 initiator（旧数据/未传）时跳过校验，向后兼容。"""
    with _lock:
        items = _fresh(_load())
        picked = None
        if pid:
            for it in items:
                if it["pid"] == pid:
                    picked = it
                    break
        elif items:
            picked = items[-1]
        rejected = False
        if picked is not None:
            initiator = picked.get("initiator")
            if operator is not None and initiator and operator != initiator:
                print(f"[approval] ⚠ 拒绝：operator={operator} 与发起人 initiator={initiator} 不一致，"
                      f"pid={picked['pid']}（记录保留，未消费）", flush=True)
                rejected = True
            else:
                items = [it for it in items if it["pid"] != picked["pid"]]
        _save(items)   # 无论是否取到/是否放行，都落盘一次（顺带清理过期条目，行为与改前一致）
    return None if (picked is None or rejected) else picked


def peek():
    """看最新一条待审（不移除）。"""
    items = _fresh(_load())
    return items[-1] if items else None


def count():
    return len(_fresh(_load()))
