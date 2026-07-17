# -*- coding: utf-8 -*-
"""
mgj-spark 桥（精简总控 · Linux/ARM 原生）
==========================================
猫管家的 Spark 精简版：管一台 DGX Spark 上「已部署一半的 AIGC-spark」。
保留猫管家的安全内核——审批门(T0~T3) + Token 发证 + 分层记忆 + 台账；
砍掉 Windows 强绑定（.bat/.vbs/PowerShell、写死的 lark-cli 路径）；
意图分类升级为插件（plugins/），飞书通道可切（http / larkcli）。

消息处理链：去重 → 本人校验 → 控制命令 → 硬阻断 → 插件路由 → 定档 →
            (T2/T3 审批门) → 发证 → 插件执行 → 台账 → 回复。

用法：
  python bridge.py --selftest        本地体检（不消费飞书）
  python bridge.py --once "问题"      本地直跑一次（不经飞书）
  python bridge.py                   正式启动，消费飞书消息
"""
import os
import sys
import json
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from core import broker, approval, ledger, intent  # noqa: E402
from core.registry import settings  # noqa: E402
from channel.base import make_channel  # noqa: E402
from plugins import base as plugins  # noqa: E402

STATE_DIR = os.path.join(HERE, "_state")
os.makedirs(STATE_DIR, exist_ok=True)
PROCESSED_FILE = os.path.join(STATE_DIR, "processed.json")
LOCK_FILE = os.path.join(STATE_DIR, "bridge.lock")
CONFIG_DIR = os.path.join(HERE, "config")


# ---------------- 日志 ----------------
def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ---------------- 配置 ----------------
def load_credentials():
    path = os.path.join(CONFIG_DIR, "credentials.json")
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


CREDS = load_credentials()
OWNER_OPEN_ID = CREDS.get("user_open_id", "")


# ---------------- 去重 ----------------
# 用 dict 当「保序集合」：dict 保插入序，list(d)[-2000:] 稳定保留最近处理的 2000 条。
# （曾用 set，但 set 无序，[-2000:] 会随机丢弃刚处理的 message_id → 重启/断线重投后
#  被丢的 id 会被当新消息重复处理：重复单发 LLM / 重复登记审批。故改 dict。）
def load_processed():
    try:
        return dict.fromkeys(json.load(open(PROCESSED_FILE, encoding="utf-8")))
    except Exception:
        return {}


def save_processed(d):
    try:
        json.dump(list(d)[-2000:], open(PROCESSED_FILE, "w", encoding="utf-8"))
    except Exception:
        pass


# ---------------- 单实例锁（跨平台）----------------
def acquire_single_instance():
    """PID 文件锁：存活的旧实例在 → 拒绝启动。绝不误杀。"""
    try:
        if os.path.exists(LOCK_FILE):
            old = (open(LOCK_FILE, encoding="utf-8").read() or "").strip()
            if old.isdigit() and _pid_alive(int(old)):
                return False
        open(LOCK_FILE, "w", encoding="utf-8").write(str(os.getpid()))
        return True
    except Exception:
        return True


def _pid_alive(pid):
    try:
        if os.name == "nt":
            import subprocess
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                               capture_output=True, text=True, timeout=10)
            return str(pid) in (r.stdout or "")
        os.kill(pid, 0)
        return True
    except Exception:
        return False


# ---------------- 通道（延迟建，selftest/once 也能用发送）----------------
_CHANNEL = {"obj": None}


def channel():
    if _CHANNEL["obj"] is None:
        mode = settings().get("feishu_channel", "http")
        _CHANNEL["obj"] = make_channel(mode, CREDS)
    return _CHANNEL["obj"]


def reply_to_user(open_id, reply):
    """把插件 Reply 发出去：卡片走 send_card，纯文本走 send_text。"""
    ch = channel()
    if reply.is_card:
        ch.send_card(open_id, reply.title or "🐱 mgj-spark", reply.body or reply.text or "",
                     template=reply.template, urls=reply.urls, actions=reply.actions,
                     image_paths=reply.image_paths)
    else:
        ch.send_text(open_id, reply.text or "(空)")


# ---------------- 控制命令 ----------------
def handle_control(open_id, text):
    """返回 True 表示已作为控制命令处理（不再走插件）。"""
    t = (text or "").strip()
    low = t.lower()
    if t in ("帮助", "help", "菜单", "?", "？"):
        channel().send_card(open_id, "🐱 mgj-spark 帮助",
                            "我是跑在 Spark 上的精简猫管家，管这台机器的 AIGC-spark 生图。\n"
                            "**能问我：**\n"
                            "· 「spark 状态 / 生图好了吗 / ComfyUI 在线吗 / 部署到哪步了」→ 查运维态\n"
                            "· 其它问题 → 大模型作答（默认 StepFun）\n"
                            "**控制命令：** 批准 / 取消 / 状态 / 帮助",
                            template="blue")
        return True
    if t in ("状态", "台账", "status"):
        s = ledger.summary()
        tot = s["total"]
        body = (f"任务 {tot['tasks']} 单 · 失败 {tot['fail']} · "
                f"token 入{tot['tokens_in']}/出{tot['tokens_out']} · ${tot['cost_usd']}\n"
                f"待审 {approval.count()} 条")
        channel().send_card(open_id, "📊 mgj-spark 台账", body, template="turquoise")
        return True
    if low in ("批准", "approve", "同意", "确认", "✅") or t.startswith("批准"):
        pending = approval.take(operator=open_id)
        if not pending:
            channel().send_text(open_id, "没有待审任务（或已过期/非你发起）")
            return True
        log(f"审批放行 pid={pending['pid']} → 执行")
        _execute(open_id, pending["text"], approved=True,
                 forced_tier=pending.get("tier"))
        return True
    if low in ("取消", "cancel", "否", "❌"):
        pending = approval.take(operator=open_id)
        channel().send_text(open_id, "已取消该待审任务" if pending else "没有待审任务")
        return True
    return False


# ---------------- 主处理 ----------------
def _execute(open_id, text, approved=False, forced_tier=None):
    """路由到插件并执行（已过审批门/或无需审批）。"""
    ctx = {"open_id": open_id, "owner": OWNER_OPEN_ID, "channel": channel(),
           "settings": settings()}
    plugin, score = plugins.route(text, ctx)
    if plugin is None:
        channel().send_text(open_id, "没有可用插件处理这条消息")
        return
    node_id = plugin.node()
    tier = forced_tier if forced_tier is not None else plugin.tier(text, ctx)
    rec = broker.issue(node_id, text, tier, ttl_seconds=settings().get("token_ttl_seconds", 1800))
    t0 = time.time()
    ok = True
    meta = {}
    try:
        reply = plugin.handle(text, ctx, rec)
        meta = getattr(reply, "meta", {}) or {}
        reply_to_user(open_id, reply)
    except Exception as e:
        ok = False
        err = f"⚠ 插件 {plugin.name} 出错：{type(e).__name__}: {e}"
        log(err + "\n" + traceback.format_exc()[-600:])
        channel().send_text(open_id, err)
    finally:
        usage = meta.get("usage") or {}
        ledger.record(node=node_id, plugin=plugin.name, tier=tier,
                      intent=meta.get("intent", ""), duration_s=round(time.time() - t0, 1),
                      ok=ok, model=meta.get("model", ""),
                      tokens_in=usage.get("tokens_in", 0), tokens_out=usage.get("tokens_out", 0),
                      cost_usd=usage.get("cost_usd", 0) or 0, task=text)


def dispatch(open_id, text):
    """一条消息的完整处理链（不含去重/本人校验，那在 on_message 里）。"""
    if handle_control(open_id, text):
        return
    # 硬阻断：灾难级请求 issue 前直接不受理
    blk = intent.hard_block(text)
    if blk:
        channel().send_text(open_id, f"🚫 拒绝受理：{blk}（灾难级操作，本总控不执行）")
        return
    # 路由 + 定档
    ctx = {"open_id": open_id, "owner": OWNER_OPEN_ID, "channel": channel(), "settings": settings()}
    plugin, score = plugins.route(text, ctx)
    tier = plugin.tier(text, ctx) if plugin else 1
    # 审批门：T2/T3 不点不跑
    if tier >= 2:
        pid = approval.add(text, [plugin.node() if plugin else "general"], tier,
                           intent="", mode="single", initiator=open_id)
        tier_name = broker.TIER_NAME.get(tier, f"T{tier}")
        channel().send_card(open_id, f"🔐 需要审批（{tier_name}）",
                           f"任务：{text}\n\n这是 **{tier_name}** 级操作，回复「**批准**」执行，"
                           f"「**取消**」作废（30 分钟过期）。",
                           template="orange")
        log(f"登记待审 pid={pid} tier=T{tier}: {text[:50]}")
        return
    _execute(open_id, text, forced_tier=tier)


def on_message(msg):
    """通道回调：一条 IncomingMessage。"""
    processed = _PROCESSED
    if msg.message_id in processed:
        return
    processed[msg.message_id] = None
    save_processed(processed)
    if OWNER_OPEN_ID and msg.open_id != OWNER_OPEN_ID:
        log(f"忽略非本人消息 from={msg.open_id[:12]}…")
        return
    if msg.msg_type != "text" or not (msg.text or "").strip():
        channel().send_text(msg.open_id, "目前只处理文字消息（图片/文件暂不支持）")
        return
    log(f"收到: {msg.text[:60]}")
    try:
        dispatch(msg.open_id, msg.text.strip())
    except Exception as e:
        err = f"⚠ 处理出错：{type(e).__name__}: {e}"
        log(err + "\n" + traceback.format_exc()[-600:])
        try:
            channel().send_text(msg.open_id, err)
        except Exception:
            pass


_PROCESSED = {}   # message_id -> None，当保序集合用（见 load_processed 注释）


# ---------------- 模式：selftest ----------------
def selftest():
    print("=== mgj-spark 体检 ===")
    ok_all = True
    # 1 凭证 + 通道
    mode = settings().get("feishu_channel", "http")
    print(f"[1] 飞书通道 = {mode}")
    ch = make_channel(mode, CREDS)
    ok, detail = ch.selftest()
    print(f"    {'✅' if ok else '⚠'} {detail}")
    ok_all &= ok or (not CREDS)   # 没配凭证时不算硬失败（本地开发）
    # 2 插件
    pls = plugins.load_plugins(force=True)
    print(f"[2] 插件 {len(pls)} 个: " + ", ".join(f"{p.name}(pri={p.priority})" for p in pls))
    ok_all &= len(pls) >= 1
    # 3 provider 侦测
    try:
        from core import providers
        det = providers.detect()
        avail = [p for p, v in det.items() if v.get("ok")]
        print(f"[3] 可用 provider: {avail or '（无——配 stepfun_api_key）'}")
        prov, model, gated = providers.resolve(
            settings().get("model_policy", {}).get("default", "stepfun"), fallback="stepfun")
        print(f"    默认模型解析 → {prov}:{model}{'（降级）' if gated else ''}")
    except Exception as e:
        print(f"[3] provider 侦测异常: {e}")
    # 4 审批门/发证/台账 自检
    r = broker.issue("selftest", "体检任务", 1, ttl_seconds=5)
    vok = broker.validate(r["token"], "selftest")[0]
    print(f"[4] 发证/校验: {'✅' if vok else '❌'}  待审={approval.count()}  台账={ledger.summary()['total']['tasks']}单")
    ok_all &= vok
    # 5 spark_ops 采集（真连本机端点）
    try:
        from plugins.spark_ops import collect_all
        print("[5] Spark 运维态采集:")
        for row in collect_all():
            ic = {True: "🟢", False: "🔴", None: "⚪"}.get(row["online"], "⚪")
            print(f"    {ic} {row['name']} — {row['detail']}")
    except Exception as e:
        print(f"[5] spark_ops 异常: {e}")
    # 6 生图链路（spark_gen）：档位 + ComfyUI 预检
    try:
        from core import comfyui_client as cc
        cfg = cc.active_config()
        print(f"[6] 生图 spark_gen: 档位={cfg['profile']}/{cfg['flux_variant']} 端点={cfg['comfy_url']} "
              f"tier=T{settings().get('gen_tier', 1)}")
        if cc.is_available():
            pf = cc.preflight("flux")
            if pf.get("ok"):
                print(f"    ✅ ComfyUI 在线，preflight 通过（unet={cfg['flux'].get('unet','')}）")
            else:
                print(f"    ⚠ ComfyUI 在线但缺件: 节点{pf.get('missing_nodes')} 模型{pf.get('missing_models')}")
        else:
            print(f"    ⚪ ComfyUI 未在线（{cfg['comfy_url']}）——起 ComfyUI 后即可出图")
    except Exception as e:
        print(f"[6] 生图自检异常: {e}")
    print("=== " + ("就绪 ✅" if ok_all else "有告警 ⚠（见上）") + " ===")
    return ok_all


# ---------------- 模式：once ----------------
def run_once(text):
    print(f"=== once: {text} ===")

    class _Cap:
        """本地直跑：把回复打到控制台，不发飞书。"""
        def send_text(self, oid, t):
            print("[reply-text]\n" + (t or ""))
            return True

        def send_card(self, oid, title, body, template="blue", urls=None, actions=None,
                      image_paths=None):
            print(f"[reply-card · {template}] {title}\n{body}")
            if urls:
                print("  links:", urls)
            if image_paths:
                print("  images:", image_paths)
            return True

        def send_image(self, oid, image_path):
            print("[reply-image]", image_path)
            return True
    _CHANNEL["obj"] = _Cap()
    dispatch(OWNER_OPEN_ID or "local", text)


# ---------------- 模式：consume ----------------
def consume_loop():
    global _PROCESSED
    if not acquire_single_instance():
        log("⚠ 已有另一个 mgj-spark 桥在运行（锁被占用），本进程退出。")
        return
    if not (CREDS.get("app_id") and CREDS.get("app_secret") and OWNER_OPEN_ID):
        log("致命：config/credentials.json 缺 app_id/app_secret/user_open_id")
        return
    _PROCESSED = load_processed()
    ch = channel()
    log(f"mgj-spark 桥启动 pid={os.getpid()} 通道={settings().get('feishu_channel','http')} "
        f"插件={len(plugins.load_plugins())} 已处理={len(_PROCESSED)}条")
    ch.start_consume(on_message)


# ---------------- 入口 ----------------
if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        sys.exit(0 if selftest() else 1)
    elif args and args[0] == "--once":
        run_once(" ".join(args[1:]) or "spark 状态")
    else:
        consume_loop()
