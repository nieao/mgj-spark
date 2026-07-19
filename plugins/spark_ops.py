# -*- coding: utf-8 -*-
"""
Spark 运维管家插件（mgj-spark 的核心能力）
============================================
管「已部署一半的 AIGC-spark」在 DGX Spark 上的运行态：
  · aeon-spark ComfyUI  (默认 127.0.0.1:8188)  —— 生图引擎
  · AIGC-spark 生图工作台 (默认 127.0.0.1:8265) —— 本项目 Web
  · spark-keeper 部署代理  (~/spark-deploy/status.json / deploy.log) —— 下载/部署进度
  · GPU (nvidia-smi)     —— GB10 温度/驱动
只读（T0），飞书里问「生图服务好了吗 / ComfyUI 在线吗 / 部署到哪一步了 / GPU 怎样 / spark 状态」即答。

端点/路径全走 env（.env），本机跑就用 localhost；也可指到远端 spark 做「本地遥测」。
绝不改任何东西——纯观测。真正要动手部署走 general_qa + 审批门 或人工。
"""
import os
import json
import shutil
import subprocess

from plugins.base import Plugin, Reply

def _env(name, default):
    """读环境变量并剥掉行内 # 注释与首尾空白。
    根治：systemd EnvironmentFile 不剥 .env 行内注释，会把「值 # 注释」整段当值 →
    URL 里混进注释导致 InvalidURL。此处防御性清洗，脏 .env 也不崩。"""
    v = os.environ.get(name)
    if v is None:
        return default
    v = v.split("#", 1)[0].strip()
    return v or default


COMFY_URL = _env("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
AIGC_URL = _env("AIGC_URL", "http://127.0.0.1:8265").rstrip("/")
SPARK_DEPLOY_DIR = os.path.expanduser(_env("SPARK_DEPLOY_DIR", "~/spark-deploy"))

# 只读观测插件：只接「查状态/问进度」的观测型消息，绝不抢「部署/安装/生成」这类动作请求
# （动作请求应落到 general_qa，由 core.intent 定档，该升 T2 就走审批门）。
_STRONG = ("comfyui", "aeon-spark", "生图服务", "出图服务", "spark-keeper", "aigc-spark")  # 几乎必是观测目标
_SOFT = ("spark", "生图", "出图", "comfy", "aigc", "flux", "工作台", "gpu", "显卡", "部署", "模型")
_STATUS_SIG = ("状态", "在线", "好了", "到哪", "进度", "健康", "怎么样", "怎样", "是否",
               "正常", "起来", "挂了", "死了", "查一下", "看看", "多少", "running",
               "status", "ok吗", "跑起来", "就绪", "情况")
_ACTION_VERB = ("部署", "安装", "启动", "重启", "下载", "拉起", "配置", "设置",
                "生成", "画一", "出一张", "帮我生", "训练", "跑一下", "改")


def _http_json(url, timeout=4):
    """GET 一个 JSON 端点。返回 (ok, data_or_errstr)。绝不抛。"""
    try:
        import requests
    except Exception:
        return False, "requests 不可用"
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return False, f"http {r.status_code}"
        try:
            return True, r.json()
        except Exception:
            return True, {"_raw": (r.text or "")[:200]}
    except Exception as e:
        return False, f"{type(e).__name__}"


def _tcp_alive(url, timeout=3):
    """只测端口活否（不要求 200），给没有 JSON 健康端点的服务用。"""
    try:
        import requests
        requests.get(url, timeout=timeout)
        return True
    except Exception as e:
        # 连上但返回非 2xx 也算「在线」；只有连不上才算死
        return "ConnectionError" not in type(e).__name__ and "Timeout" not in type(e).__name__


def _comfy_status():
    ok, data = _http_json(f"{COMFY_URL}/system_stats")
    if not ok:
        return {"name": "ComfyUI(aeon-spark)", "online": False, "detail": f"{COMFY_URL} 不可达（{data}）"}
    detail = ""
    try:
        dev = (data.get("devices") or [{}])[0]
        vram = dev.get("vram_total")
        if vram:
            detail = f"{dev.get('name', 'GPU')} · VRAM {round(int(vram)/(1024**3))}G"
    except Exception:
        pass
    return {"name": "ComfyUI(aeon-spark)", "online": True, "detail": detail or "在线", "url": COMFY_URL}


def _aigc_status():
    alive = _tcp_alive(AIGC_URL)
    return {"name": "AIGC-spark 生图工作台", "online": alive,
            "detail": "在线" if alive else f"{AIGC_URL} 不可达", "url": AIGC_URL}


def _keeper_status():
    """读 spark-keeper 落的部署进度。"""
    st = os.path.join(SPARK_DEPLOY_DIR, "status.json")
    if not os.path.exists(st):
        return {"name": "spark-keeper 部署进度", "online": None,
                "detail": f"无 {st}（尚未跑过部署代理或非本机）"}
    try:
        d = json.load(open(st, encoding="utf-8"))
        job = d.get("job") or d.get("task") or "?"
        stage = d.get("stage") or d.get("step") or ""
        pct = d.get("percent") or d.get("progress")
        state = d.get("state") or d.get("status") or ""
        bits = [f"任务={job}"]
        if stage:
            bits.append(f"阶段={stage}")
        if pct is not None:
            bits.append(f"{pct}%")
        if state:
            bits.append(state)
        return {"name": "spark-keeper 部署进度", "online": None, "detail": " · ".join(bits)}
    except Exception as e:
        return {"name": "spark-keeper 部署进度", "online": None, "detail": f"status.json 解析失败:{e}"}


def _gpu_status():
    if not shutil.which("nvidia-smi"):
        return {"name": "GPU", "online": None, "detail": "无 nvidia-smi（非 spark 或未装驱动）"}
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,temperature.gpu,driver_version",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10)
        line = (r.stdout or "").strip().splitlines()
        if line:
            name, temp, drv = [x.strip() for x in (line[0].split(",") + ["", "", ""])[:3]]
            return {"name": "GPU", "online": True, "detail": f"{name} · {temp}°C · 驱动 {drv}"}
    except Exception as e:
        return {"name": "GPU", "online": None, "detail": f"nvidia-smi 失败:{e}"}
    return {"name": "GPU", "online": None, "detail": "无输出"}


def collect_all():
    """汇总四路运维态。供插件 handle 和 bridge --selftest 复用。"""
    return [_comfy_status(), _aigc_status(), _keeper_status(), _gpu_status()]


class SparkOpsPlugin(Plugin):
    name = "spark_ops"
    priority = 10          # 高于兜底
    node_id = "aigc-spark"

    def can_handle(self, text, ctx):
        t = (text or "").lower()
        if not t:
            return 0.0
        has_status = any(w in t for w in _STATUS_SIG)
        has_action = any(w in t for w in _ACTION_VERB)
        # 强观测目标（comfyui/aeon-spark…）几乎必是问状态 → 高分接
        if any(w in t for w in _STRONG):
            return 0.85
        # 出现动作词且没有任何状态信号 → 这是「动作请求」，让给 general_qa 走审批定档
        if has_action and not has_status:
            return 0.0
        soft = any(w in t for w in _SOFT)
        if soft and has_status:
            return 0.7        # 「spark 状态怎么样 / 部署到哪一步了」
        if soft and not has_action:
            return 0.4        # 裸提域内词（「看下生图」）——弱接，仍会被更强插件盖过
        return 0.0

    def tier(self, text, ctx):
        return 0            # 纯只读

    def handle(self, text, ctx, rec):
        rows = collect_all()
        icon = {True: "🟢", False: "🔴", None: "⚪"}
        lines = []
        for r in rows:
            lines.append(f"{icon.get(r['online'], '⚪')} **{r['name']}** — {r['detail']}")
        # 一句话总结
        comfy_on = rows[0]["online"]
        aigc_on = rows[1]["online"]
        if comfy_on and aigc_on:
            head = "生图链路已就绪 ✅（ComfyUI + 工作台都在线）"
        elif comfy_on and not aigc_on:
            head = "ComfyUI 在线，但 AIGC-spark 工作台没起 —— 去 spark 上 `./start.sh`"
        elif not comfy_on:
            head = "ComfyUI 还没在线 —— 先拉起 aeon-spark 的 ComfyUI（8188）"
        else:
            head = "部分服务在线"
        body = head + "\n\n" + "\n".join(lines)
        urls = []
        if aigc_on:
            urls.append({"text": "打开生图工作台", "url": AIGC_URL})
        return Reply(title="🐱 Spark 运维态", body=body, template="turquoise", urls=urls,
                     meta={"tier": 0, "kind": "spark_status"})
