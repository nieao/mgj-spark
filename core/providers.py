# -*- coding: utf-8 -*-
"""多 provider 层：让猫管家不只调 claude，也能调 codex / ollama / 国内云端 API —— 不同模型干不同的事。
=================================================================================
- detect()：自动侦测本机有哪些 provider 可用 + 各自模型（"系统自动侦测每个默认选择的门控"）。
- resolve(spec)：把 model 规格(如 'codex:gpt-5.1-codex' / 'opus' / 'deepseek' / 'glm:glm-4.6')解析成
  (provider, model)，并做**可用性门控**——指到没装/没配 key 的 provider 自动降级回 claude 默认。
- complete(provider, model, system, user)：单发一次补全（MoA 顾问/聚合、跨模型理智检查、路由判官都用）。
  claude 走 -p json；codex 走 `exec -c mcp_servers={}`(关 MCP 防 X OAuth 弹窗) + -o 取纯末条；
  ollama 走 `run` stdin；agnes/deepseek/glm/kimi/hunyuan 等走 OpenAI 兼容 HTTP。
  codex/ollama 无 token 计费 → usage=None(诚实,成本按 pricing 能识别才估)。

跨 provider 切换的两个入口：
  · 安装部署时：config/credentials.json 填对应 key（或设同名环境变量），registry.json 的
    model_policy 把某类/某意图默认模型改成 'deepseek'/'glm' 等即可。
  · 使用中：飞书里说『用deepseek/用glm/用kimi/用混元 干X』临时切（router.detect_provider_override）。
  · 任意扩展：config/providers.json 覆盖内置或新增任意 OpenAI 兼容 provider，无需改代码。

铁律：本模块只做"单发补全"，不碰权限档/预算硬顶（那是 dispatcher 的事）。任何异常都返回
(None, err) 让上层优雅降级，绝不抛。
"""
import os, json, subprocess, tempfile, time

APPDATA = os.environ.get("APPDATA", os.path.expanduser("~/AppData/Roaming"))
CLAUDE_EXE = os.path.join(APPDATA, "npm", "node_modules", "@anthropic-ai",
                          "claude-code", "bin", "claude.exe")
CODEX_EXE = os.path.join(APPDATA, "npm", "codex.cmd")   # npm 装的 codex CLI（.cmd 包装）

# ============================================================================
# OpenAI 兼容 provider 内置注册表（云端 / 国内 API）
# ----------------------------------------------------------------------------
# 这些都是标准 OpenAI 兼容 /chat/completions 接口，一套 _openai_compat_complete 全覆盖。
# base_url 是公开信息（开箱即用，别人不用查端点）；api_key 是机密，从 env 或
# config/credentials.json 读（gitignore 不入库）。
# 全部可被 config/providers.json 覆盖/新增——即使某端点/模型名将来变了，改 config 即可，
# 无需动代码（能力自愈，不写死）。
#   key_env   : 环境变量名（安装部署首选：set/export 一个 env 即启用，使用中也可临时改）
#   key_field : config/credentials.json 里的字段名（另一种配法）
#   free      : True=免费档，成本诚实标 0；否则有 token 消耗但本层不臆测价（cost=None，交上层估）
# 以下 base_url + 模型名 2026-07-14 经官方核对（各家 /v1 端点均已 401 直连验证可达）。
# 国内模型若上线更新型号（如 glm-5），把对应 default_model / models 改掉即可。
# ============================================================================
OPENAI_COMPAT = {
    "stepfun": {
        "base_url": "https://api.stepfun.com/v1", "default_model": "step-3.7-flash",
        "models": ["step-3.7-flash", "step-3", "step-2-16k", "step-1-8k"],
        "key_env": "STEPFUN_API_KEY", "key_field": "stepfun_api_key",
        "free": False, "note": "阶跃星辰 StepFun（3.7-flash=默认快档；OpenAI 兼容）",
    },
    "agnes": {
        "base_url": "https://apihub.agnes-ai.com/v1", "default_model": "agnes-1.5-flash",
        "models": ["agnes-1.5-flash", "agnes-2.0-flash"],
        "key_env": "AGNES_API_KEY", "key_field": "agnes_api_key",
        "free": True, "note": "Agnes AI 免费 OpenAI 兼容网关",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1", "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "key_env": "DEEPSEEK_API_KEY", "key_field": "deepseek_api_key",
        "free": False, "note": "深度求索（chat=V3.2非思考 / reasoner=思考模式）",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4", "default_model": "glm-4.6",
        "models": ["glm-4.6", "glm-4.5", "glm-4.5-air", "glm-4.5-flash", "glm-4-plus"],
        "key_env": "ZHIPU_API_KEY", "key_field": "zhipu_api_key",
        "free": False, "note": "智谱 GLM（4.6=旗舰200K；有更新型号直接改 default_model）",
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1", "default_model": "kimi-k2-0905-preview",
        "models": ["kimi-k2-0905-preview", "kimi-k2-0711-preview",
                   "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "key_env": "MOONSHOT_API_KEY", "key_field": "moonshot_api_key",
        "free": False, "note": "月之暗面 Kimi / Moonshot（k2 为新一代）",
    },
    "hunyuan": {
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1", "default_model": "hunyuan-turbos-latest",
        "models": ["hunyuan-turbos-latest", "hunyuan-turbo", "hunyuan-pro",
                   "hunyuan-standard", "hunyuan-lite"],
        "key_env": "HUNYUAN_API_KEY", "key_field": "hunyuan_api_key",
        "free": False, "note": "腾讯混元（turbos-latest 为最新 Turbo）",
    },
}


def _load_provider_overrides():
    """从 config/providers.json 合并用户自定义（可覆盖内置 base_url/models/default_model，
    或新增任意 OpenAI 兼容 provider）。文件不存在则用内置表。绝不抛。"""
    try:
        cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "config", "providers.json")
        if not os.path.exists(cfg):
            return
        d = json.load(open(cfg, encoding="utf-8"))
        for pid, conf in (d.get("openai_compat") or {}).items():
            if not isinstance(conf, dict):
                continue
            merged = dict(OPENAI_COMPAT.get(pid, {}))
            merged.update({k: v for k, v in conf.items() if not str(k).startswith("_")})
            merged.setdefault("free", False)
            merged.setdefault("models", [])
            OPENAI_COMPAT[pid] = merged
    except Exception:
        pass


_load_provider_overrides()

# 默认模型（各 provider 缺省用哪个）——CLI 类手写，OpenAI 兼容类从注册表自动填。
DEFAULT_MODELS = {
    "claude": None,                    # None = 用 claude CLI 默认（不加 --model）
    "codex": "gpt-5.5",               # 用户 codex 走 ChatGPT 账号，只支持 gpt-5.5(gpt-5.1-codex 会 400)
    "ollama": "qwen3:4b",              # 本机实拉的本地模型（detect 会列真实清单）
}
for _pid, _conf in OPENAI_COMPAT.items():
    DEFAULT_MODELS[_pid] = _conf.get("default_model")

CLAUDE_ALIASES = {"opus", "sonnet", "haiku", "opus-4-8", "sonnet-5", "haiku-4-5"}

_detect_cache = {"val": None, "ts": 0}
_DETECT_TTL = 300   # 侦测结果缓存 5 分钟（装/卸 CLI 不频繁）


def _claude_bin():
    return CLAUDE_EXE if os.path.exists(CLAUDE_EXE) else "claude"


def _codex_bin():
    if os.path.exists(CODEX_EXE):
        return CODEX_EXE
    alt = os.path.join(APPDATA, "npm", "codex")
    return alt if os.path.exists(alt) else "codex"


def _which(name):
    """跨平台探命令是否在 PATH。"""
    try:
        r = subprocess.run(["where" if os.name == "nt" else "which", name],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=10)
        return r.returncode == 0 and bool((r.stdout or "").strip())
    except Exception:
        return False


# ---------------- 自动侦测（门控真相源）----------------
def detect(force=False):
    """探本机可用 provider + 模型。返回 {provider: {"ok":bool, "models":[...], "note":str}}。缓存 5 分钟。"""
    now = time.time()
    if not force and _detect_cache["val"] is not None and now - _detect_cache["ts"] < _DETECT_TTL:
        return _detect_cache["val"]
    out = {}
    # claude —— 猫管家的地基，几乎总在
    out["claude"] = {"ok": os.path.exists(CLAUDE_EXE) or _which("claude"),
                     "models": ["opus", "sonnet", "haiku"], "note": ""}
    # codex —— npm 装的 CLI
    cok = os.path.exists(CODEX_EXE) or os.path.exists(os.path.join(APPDATA, "npm", "codex")) or _which("codex")
    out["codex"] = {"ok": cok, "models": [DEFAULT_MODELS["codex"]] if cok else [],
                    "note": "ChatGPT账号,用gpt-5.5" if cok else "未装 codex CLI"}
    # ollama —— 本地模型，列出实际拉了哪些
    oll_models = []
    oll_ok = _which("ollama") or os.path.exists(
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"))
    if oll_ok:
        try:
            r = subprocess.run(["ollama", "list"], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=15)
            for ln in (r.stdout or "").splitlines()[1:]:
                name = ln.split()[0] if ln.split() else ""
                if name:
                    oll_models.append(name)
        except Exception:
            pass
    out["ollama"] = {"ok": oll_ok, "models": oll_models,
                     "note": "" if oll_ok else "未装 ollama"}
    # OpenAI 兼容云端/国内 provider —— 有 key 即可用（agnes/deepseek/glm/kimi/hunyuan 及自定义）
    for pid, conf in OPENAI_COMPAT.items():
        k = _load_key(pid)
        out[pid] = {"ok": bool(k),
                    "models": list(conf.get("models") or []) if k else [],
                    "note": conf.get("note", "") if k else f"未配置 {conf.get('key_env', pid.upper() + '_API_KEY')}"}
    _detect_cache["val"] = out
    _detect_cache["ts"] = now
    return out


def available_providers():
    return [p for p, v in detect().items() if v.get("ok")]


# ---------------- 规格解析 + 可用性门控 ----------------
def resolve(spec, fallback="claude"):
    """把 model 规格解析成 (provider, model)，并做可用性门控。
    spec 形如 'codex:gpt-5.1-codex' / 'deepseek:deepseek-chat' / 'glm' / 'opus'(裸claude别名) / '' / None。
    provider 不可用时降级回 fallback(默认 claude) 的默认模型，返回时带降级标记。
    返回 (provider, model, gated)。gated=True 表示发生了门控降级。"""
    det = detect()
    provider, model = "claude", None
    s = (spec or "").strip()
    if ":" in s:
        p, m = s.split(":", 1)
        provider, model = p.strip().lower(), m.strip() or None
    elif s and s.lower() not in CLAUDE_ALIASES:
        # 裸名但不是 claude 别名 —— 可能是 provider 名(codex/deepseek/glm…)或某 provider 的模型名
        if s.lower() in det:
            provider, model = s.lower(), DEFAULT_MODELS.get(s.lower())
        else:
            provider, model = "claude", s     # 当成 claude 模型名
    else:
        provider, model = "claude", (s or None)
    # 门控：provider 不可用 → 降级
    if not det.get(provider, {}).get("ok"):
        fb = fallback if det.get(fallback, {}).get("ok") else "claude"
        return fb, DEFAULT_MODELS.get(fb), True
    if provider == "claude" and model is None:
        return "claude", None, False
    if model is None:
        model = DEFAULT_MODELS.get(provider)
    return provider, model, False


# ---------------- 单发补全 ----------------
def build_command(provider, model, system, user, reasoning="low"):
    """构造单发补全命令。返回 (argv, stdin_text, out_file_or_None)。dry 测试可直接查。"""
    if provider == "codex":
        out_file = os.path.join(tempfile.gettempdir(), f"mgj_codex_{os.getpid()}_{int(time.time() * 1000) % 100000}.txt")
        argv = [_codex_bin(), "exec", "--skip-git-repo-check",
                "-c", "mcp_servers={}",                 # 关全部 MCP：防 X OAuth 弹窗 + 快启动
                "-c", f"model_reasoning_effort={reasoning}",
                "--model", model or DEFAULT_MODELS["codex"], "-o", out_file]
        stdin = f"[System]\n{system}\n\n[User]\n{user}" if system else user
        return argv, stdin, out_file
    if provider == "ollama":
        argv = [_which_ollama(), "run", model or DEFAULT_MODELS["ollama"]]
        stdin = f"{system}\n\n{user}" if system else user
        return argv, stdin, None
    # claude 单发（json，纯问答不需工具；不加 --allowed-tools 让它直接答）
    argv = [_claude_bin(), "-p", "--output-format", "json", "--dangerously-skip-permissions"]
    if model and model not in (None, ""):
        argv += ["--model", model]
    if system:
        argv += ["--append-system-prompt", system]
    return argv, user, None


def _which_ollama():
    o = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    return o if os.path.exists(o) else "ollama"


def _kill_tree(proc):
    """Windows 上杀整棵进程树。codex.cmd 会派生 node，光 kill 到 cmd 层杀不干净，
    会导致 communicate 永久阻塞（Fable 核查抓出的锁中毒根因）。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _cleanup(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def complete(provider, model, system, user, timeout=120, reasoning="low", cwd=None):
    """单发一次补全。返回 (text, meta)。text=None 表示失败(meta['error'] 有原因)。
    meta 含 provider/model/duration_s/usage(能拿到才有,否则 None)。绝不抛异常。
    cwd 非空时子进程在该目录跑(codex/claude 可在项目内用工具改文件=agentic 干活)。"""
    provider = (provider or "claude").lower()
    if provider in OPENAI_COMPAT:      # 云端/国内 OpenAI 兼容 provider 走 HTTP, 不经 subprocess
        return _openai_compat_complete(provider, model, system, user, timeout=timeout, reasoning=reasoning)
    t0 = time.time()
    try:
        argv, stdin, out_file = build_command(provider, model, system, user, reasoning=reasoning)
    except Exception as e:
        return None, {"provider": provider, "model": model, "error": f"构造命令失败:{type(e).__name__}"}
    # 用 Popen + 超时树杀（不用 subprocess.run）：run 在 Windows 超时后只 kill 到 cmd 层，
    # codex.cmd 派生的 node 不死 → communicate 二次等 EOF 永久阻塞 → 持锁线程挂死（Fable 抓出）。
    class _R:
        stdout = ""; stderr = ""; returncode = -1
    r = _R()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                                cwd=cwd if (cwd and os.path.isdir(cwd)) else None)
        try:
            r.stdout, r.stderr = proc.communicate(input=stdin, timeout=timeout)
            r.returncode = proc.returncode
        except subprocess.TimeoutExpired:
            _kill_tree(proc)                       # 树杀，防孤儿 node 挂死持锁
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            _cleanup(out_file)
            return None, {"provider": provider, "model": model,
                          "duration_s": round(time.time() - t0, 1), "error": f"超时(>{timeout}s),已树杀"}
    except FileNotFoundError:
        _cleanup(out_file)
        return None, {"provider": provider, "model": model, "error": f"{provider} 命令不存在"}
    except Exception as e:
        _cleanup(out_file)
        return None, {"provider": provider, "model": model, "error": f"{type(e).__name__}:{e}"}

    dur = round(time.time() - t0, 1)
    meta = {"provider": provider, "model": model or "", "duration_s": dur, "usage": None, "error": ""}

    if provider == "codex":
        text = ""
        try:
            if out_file and os.path.exists(out_file):
                with open(out_file, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
                os.remove(out_file)
        except Exception:
            pass
        _cleanup(out_file)                          # 兜底清理(读取异常时也不残留)
        if not text and r.returncode != 0:
            meta["error"] = f"codex rc={r.returncode}: {(r.stderr or '')[:200]}"
            return None, meta
        return (text or (r.stdout or "").strip() or None), meta

    if provider == "ollama":
        if r.returncode != 0:
            meta["error"] = f"ollama rc={r.returncode}: {(r.stderr or '')[:200]}"
            return None, meta
        return ((r.stdout or "").strip() or None), meta

    # claude：解析 json 信封取 .result + usage
    if r.returncode != 0 and not (r.stdout or "").strip():
        meta["error"] = f"claude rc={r.returncode}: {(r.stderr or '')[:200]}"
        return None, meta
    s = (r.stdout or "").strip()
    try:
        env = json.loads(s)
        if isinstance(env, dict):
            if env.get("total_cost_usd") is not None or env.get("usage"):
                u = env.get("usage") or {}
                meta["usage"] = {
                    "tokens_in": int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                                 + int(u.get("cache_creation_input_tokens") or 0),
                    "tokens_out": int(u.get("output_tokens") or 0),
                    "cost_usd": float(env.get("total_cost_usd") or 0),
                }
                if env.get("model"):
                    meta["model"] = str(env["model"])
            s = str(env.get("result") if "result" in env else s).strip()
    except Exception:
        pass
    return (s or None), meta


# ---------------- 云端/国内 OpenAI 兼容 provider（agnes/deepseek/glm/kimi/hunyuan/自定义）----------------
def _load_key(provider):
    """通用 API key 装载，三级优先：
      ① 环境变量（conf.key_env，安装部署首选：set 一个 env 即启用，使用中也可临时改）
      ② config/credentials.json 的 conf.key_field 字段（另一种配法）
      ③ 可选外部 keyfile（环境变量 MGJ_KEYFILE 指向一个 JSON，键名同 key_env）——
         默认不设即跳过（别人零影响）；本机可 set MGJ_KEYFILE 复用已有的 key 文件。
    凭证均不入库。返回 '' 表示未配置。绝不抛。"""
    conf = OPENAI_COMPAT.get(provider, {})
    env_name = conf.get("key_env") or f"{provider.upper()}_API_KEY"
    k = os.environ.get(env_name)
    if k:
        return k.strip()
    field = conf.get("key_field") or f"{provider}_api_key"
    try:
        cred = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config", "credentials.json")
        d = json.load(open(cred, encoding="utf-8"))
        if d.get(field):
            return str(d[field]).strip()
    except Exception:
        pass
    keyfile = os.environ.get("MGJ_KEYFILE")   # 可选外部 keyfile（默认不设=跳过，可移植）
    if keyfile:
        try:
            d = json.load(open(keyfile, encoding="utf-8"))
            if d.get(env_name):               # 按 key_env 名匹配（如 AGNES_API_KEY）
                return str(d[env_name]).strip()
        except Exception:
            pass
    return ""


def _agnes_key():
    """向后兼容旧调用点。"""
    return _load_key("agnes")


def _openai_compat_complete(provider, model, system, user, timeout=120, reasoning="low"):
    """OpenAI 兼容单发（agnes/deepseek/glm/kimi/hunyuan 及 config 自定义）。
    429/5xx 指数退避重试；usage 取 prompt/completion_tokens；免费档 cost 诚实标 0，
    付费档不臆测单价（cost_usd=None，交上层 pricing 估）。返回 (text, meta)，契约同 complete()。绝不抛。"""
    conf = OPENAI_COMPAT.get(provider, {})
    mdl = model or conf.get("default_model") or DEFAULT_MODELS.get(provider)
    meta = {"provider": provider, "model": mdl, "duration_s": 0.0, "usage": None, "error": ""}
    key = _load_key(provider)
    if not key:
        meta["error"] = f"{provider}: 未配置 {conf.get('key_env', provider.upper() + '_API_KEY')}"
        return None, meta
    base_url = (conf.get("base_url") or "").rstrip("/")
    if not base_url:
        meta["error"] = f"{provider}: 未配置 base_url（config/providers.json）"
        return None, meta
    try:
        import requests
    except Exception as e:
        meta["error"] = f"{provider}: requests 不可用 {e}"
        return None, meta
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": user})
    payload = {"model": mdl, "messages": msgs, "stream": False}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    is_free = bool(conf.get("free"))
    t0 = time.time()
    last_err = ""
    ATTEMPTS = 4
    for attempt in range(ATTEMPTS):   # 限流/瞬时故障, 指数退避 1/2/4s（最后一轮不再空等）
        try:
            resp = requests.post(f"{base_url}/chat/completions", json=payload,
                                 headers=headers, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = f"{provider} http {resp.status_code}"
                if attempt < ATTEMPTS - 1:
                    time.sleep(min(2 ** attempt, 8))
                continue
            if resp.status_code != 200:
                meta["error"] = f"{provider} http {resp.status_code}: {resp.text[:200]}"
                meta["duration_s"] = round(time.time() - t0, 2)
                return None, meta
            data = resp.json()
            choices = data.get("choices") or [{}]
            text = (choices[0].get("message") or {}).get("content", "")
            u = data.get("usage") or {}
            meta["usage"] = {
                "tokens_in": int(u.get("prompt_tokens") or 0),
                "tokens_out": int(u.get("completion_tokens") or 0),
                "cost_usd": 0.0 if is_free else None,   # 免费档标0；付费档不臆测，交上层估
            }
            if data.get("model"):
                meta["model"] = str(data["model"])
            meta["duration_s"] = round(time.time() - t0, 2)
            return ((text or "").strip() or None), meta
        except Exception as e:
            last_err = f"{provider} exc: {type(e).__name__}: {e}"
            if attempt < ATTEMPTS - 1:
                time.sleep(min(2 ** attempt, 8))
    meta["error"] = last_err or f"{provider}: 重试耗尽"
    meta["duration_s"] = round(time.time() - t0, 2)
    return None, meta


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=== 侦测 provider ===")
    for p, v in detect().items():
        print(f"  {p}: {'OK可用' if v['ok'] else 'x'} 模型={v['models']} {v['note']}")
    print("=== resolve 门控演示 ===")
    for spec in ["opus", "codex:gpt-5.1-codex", "ollama:qwen2.5",
                 "deepseek", "glm:glm-4.6", "kimi", "hunyuan:hunyuan-pro",
                 "gemini:pro", ""]:
        print(f"  {spec!r:24} -> {resolve(spec)}")
