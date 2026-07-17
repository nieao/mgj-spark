# -*- coding: utf-8 -*-
"""
mgj-spark 环境自检 + 配置引导
==============================
反复跑都安全：从 *.example 生成缺失的配置文件，检查依赖/凭证/端点，告诉你还差什么。
  python setup.py            自检 + 补齐配置模板
  python setup.py --check    只检查不写文件
"""
import os
import sys
import json
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "config")


def _p(ok, msg):
    print(f"  {'✅' if ok else '❌'} {msg}")


def ensure_config(check_only):
    print("[配置文件]")
    pairs = [("credentials.example.json", "credentials.json")]
    for ex, real in pairs:
        rp = os.path.join(CFG, real)
        if os.path.exists(rp):
            _p(True, f"{real} 已存在")
            continue
        if check_only:
            _p(False, f"{real} 缺失（跑 `python setup.py` 会从 {ex} 生成）")
            continue
        shutil.copy(os.path.join(CFG, ex), rp)
        _p(True, f"已从 {ex} 生成 {real} —— 去填 app_id/app_secret/user_open_id + stepfun_api_key")


def check_deps():
    print("[依赖]")
    try:
        import requests  # noqa: F401
        _p(True, "requests 已装")
    except Exception:
        _p(False, "requests 未装 → pip install -r requirements.txt")
    try:
        import lark_oapi  # noqa: F401
        _p(True, "lark-oapi 已装（http 通道接收就绪）")
    except Exception:
        _p(False, "lark-oapi 未装（http 通道收不到消息）→ pip install lark-oapi；或用 larkcli 通道")


def check_creds():
    print("[凭证]")
    rp = os.path.join(CFG, "credentials.json")
    try:
        d = json.load(open(rp, encoding="utf-8"))
    except Exception:
        _p(False, "credentials.json 读不到/坏了")
        return
    for f in ("app_id", "app_secret", "user_open_id"):
        v = str(d.get(f, ""))
        _p(bool(v) and "你的" not in v and "ou_你" not in v, f"飞书 {f} {'已填' if v else '缺'}")
    key = d.get("stepfun_api_key") or os.environ.get("STEPFUN_API_KEY")
    _p(bool(key) and "阶跃" not in str(key), "stepfun_api_key（默认大模型）" + ("已填" if key else "缺"))


def check_registry():
    print("[注册表 & 插件]")
    sys.path.insert(0, HERE)
    try:
        from core.registry import all_nodes, settings
        _p(True, f"registry.json 加载 OK · {len(all_nodes())} 节点 · 通道={settings().get('feishu_channel')}")
    except Exception as e:
        _p(False, f"registry 加载失败: {e}")
    try:
        from plugins import base as plugins
        pls = plugins.load_plugins(force=True)
        _p(len(pls) >= 1, "插件: " + ", ".join(p.name for p in pls))
    except Exception as e:
        _p(False, f"插件加载失败: {e}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    check_only = "--check" in sys.argv
    print("=== mgj-spark setup ===")
    ensure_config(check_only)
    check_deps()
    check_creds()
    check_registry()
    print("\n下一步：填好 config/credentials.json → `python bridge.py --selftest` → `./start.sh`")
    print("Spark 生产守护见 DEPLOY-SPARK.md（systemd）。")
