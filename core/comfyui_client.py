# -*- coding: utf-8 -*-
"""
ComfyUI 出图客户端 —— 跨环境通用版（Windows 秋叶包 ↔ NVIDIA DGX Spark / comfyui-aeon-spark）

设计目标（本文件是"JSON 流通用 + 打通 ComfyUI"的核心）：
  1) 节点拓扑保持不变（ComfyUI 原生 Flux2 / Qwen-Edit 节点族，两端通用）。
  2) 模型文件名走【档位映射】：COMFY_PROFILE=aki|aeon-spark 一键切换，或用 *_UNET/*_CLIP/*_VAE 逐项 env 覆盖。
  3) 取图默认走 HTTP /view，不再强依赖读 ComfyUI 输出目录的文件系统
     —— 这样 AIGC-spark 与 ComfyUI 是否同机、ComfyUI 是否跑在 Docker 里都无所谓。
     （仍保留文件系统直取作为可选快路，见 COMFY_FETCH。）
  4) preflight()：用 /object_info 预检节点类型与模型文件是否真的在目标 ComfyUI 上，避免"提交才报缺模型"。

两条核心链路：
  · Qwen-Image-Edit-2509 多参考图编辑（★产品/角色一致性核心）
  · Flux2 纯文生图（t2i）：Windows=klein-4b；Spark=klein-9b（默认，对齐 08 号流）或 dev（FLUX_VARIANT=dev，对齐 01 号流）
"""
import os, json, time, shutil, urllib.request, urllib.parse

# 剥掉 .env 行内 # 注释（systemd EnvironmentFile 不剥，否则 URL 混进注释 → InvalidURL）
COMFY_URL = ((os.environ.get("COMFY_URL") or "").split("#", 1)[0].strip()) or "http://127.0.0.1:8188"
COMFY_ROOT = os.environ.get("COMFY_ROOT") or (r"E:\ComfyUI-aki-v2\ComfyUI" if os.name == "nt" else "")
COMFY_INPUT = os.path.join(COMFY_ROOT, "input") if COMFY_ROOT else ""
COMFY_OUTPUT = os.path.join(COMFY_ROOT, "output") if COMFY_ROOT else ""

# 取图模式：fs=直接读 ComfyUI 输出目录（同机且能读到时最快）；view=走 HTTP /view（跨机/Docker 通用，默认）
# 缺省策略：COMFY_ROOT 是一个真实存在的目录才用 fs，否则一律 view。
COMFY_FETCH = (os.environ.get("COMFY_FETCH") or ("fs" if (COMFY_OUTPUT and os.path.isdir(COMFY_OUTPUT)) else "view")).lower()

# ============ 模型档位映射（按部署环境切换文件名，这是"流通用"的关键层）============
PROFILES = {
    # 当前 Windows 秋叶整合包 E:\ComfyUI-aki-v2（本机 6 镜实跑已验证）
    "aki": {
        "qwen": {"unet": "qwen_image_edit_2509_fp8_e4m3fn.safetensors",
                 "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                 "vae":  "qwen_image_vae.safetensors"},
        "flux": {"unet": "flux-2-klein-4b.safetensors",
                 "clip": "qwen_3_4b.safetensors",
                 "vae":  "flux2-vae.safetensors", "lora": ""},
    },
    # NVIDIA DGX Spark · comfyui-aeon-spark（文件名对齐其 workflows/01、08 预置流）
    "aeon-spark": {
        # aeon-spark 未预置 Qwen-Image-Edit，参考图链路需自行下载放入 models/；此处给默认名，可 env 覆盖
        "qwen": {"unet": "qwen_image_edit_2509_fp8_e4m3fn.safetensors",
                 "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                 "vae":  "qwen_image_vae.safetensors"},
        # 文生图默认对齐 08 号 Klein-9B（质量/显存平衡，Spark 128G 统一内存可跑）
        "flux": {"unet": "flux-2-klein-base-9b-fp8.safetensors",
                 "clip": "qwen_3_8b_fp8mixed.safetensors",
                 "vae":  "full_encoder_small_decoder.safetensors", "lora": ""},
        # FLUX_VARIANT=dev 时走 01 号 Flux.2 Dev + Turbo-LoRA 加速
        "flux_dev": {"unet": "flux2_dev_fp8mixed.safetensors",
                     "clip": "mistral_3_small_flux2_bf16.safetensors",
                     "vae":  "full_encoder_small_decoder.safetensors",
                     "lora": "Flux_2-Turbo-LoRA_comfyui.safetensors"},
    },
}

COMFY_PROFILE = os.environ.get("COMFY_PROFILE") or ("aki" if os.name == "nt" else "aeon-spark")
_prof = PROFILES.get(COMFY_PROFILE, PROFILES["aki"])
_flux_key = "flux_dev" if (COMFY_PROFILE == "aeon-spark" and (os.environ.get("FLUX_VARIANT") or "").lower() == "dev") else "flux"
_flux_base = _prof.get(_flux_key, _prof["flux"])

def _pick(section, key, env):
    return os.environ.get(env) or section.get(key, "")

QWEN_MODELS = {
    "unet": _pick(_prof["qwen"], "unet", "QWEN_UNET"),
    "clip": _pick(_prof["qwen"], "clip", "QWEN_CLIP"),
    "vae":  _pick(_prof["qwen"], "vae",  "QWEN_VAE"),
}
FLUX_MODELS = {
    "unet": _pick(_flux_base, "unet", "FLUX_UNET"),
    "clip": _pick(_flux_base, "clip", "FLUX_CLIP"),
    "vae":  _pick(_flux_base, "vae",  "FLUX_VAE"),
    "lora": _pick(_flux_base, "lora", "FLUX_LORA"),
}

NEG_DEFAULT = "文字, 字幕, 水印, logo, 变形, 多余手指, 残缺, 模糊, 低质量, 暗淡"


def active_config() -> dict:
    """返回当前生效的档位/端点/取图方式，供 /api 状态展示与自检。"""
    return {"profile": COMFY_PROFILE, "flux_variant": _flux_key, "comfy_url": COMFY_URL,
            "fetch": COMFY_FETCH, "comfy_root": COMFY_ROOT or "(未设置·走/view)",
            "qwen": QWEN_MODELS, "flux": FLUX_MODELS}


# ---------- 连通性 ----------
def is_available() -> bool:
    try:
        urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=3)
        return True
    except Exception:
        return False

def is_busy() -> bool:
    try:
        with urllib.request.urlopen(f"{COMFY_URL}/queue", timeout=2) as r:
            d = json.loads(r.read().decode("utf-8"))
        return len(d.get("queue_running", [])) > 0
    except Exception:
        return False


# ---------- 参考图上传 ----------
def upload_image(local_path: str) -> str:
    """把本地图上传到 ComfyUI/input，返回它在 ComfyUI 里的文件名（供 LoadImage 用）。
    优先走 /upload/image API（跨机可用）；同机且能写 input 目录时失败兜底直接拷。"""
    if not os.path.isfile(local_path):
        raise FileNotFoundError(local_path)
    fn = os.path.basename(local_path)
    try:
        with open(local_path, "rb") as f:
            data = f.read()
        boundary = "----comfyup" + str(int(time.time() * 1000))
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{fn}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n".encode("utf-8")
            + data + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n"
            f"--{boundary}--\r\n".encode("utf-8")
        )
        req = urllib.request.Request(f"{COMFY_URL}/upload/image", data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
        return d.get("name") or fn
    except Exception:
        if COMFY_INPUT and os.path.isdir(os.path.dirname(COMFY_INPUT) or "."):
            os.makedirs(COMFY_INPUT, exist_ok=True)
            shutil.copyfile(local_path, os.path.join(COMFY_INPUT, fn))
            return fn
        raise


# ---------- 工作流构建 ----------
def build_qwen_edit_graph(prompt: str, ref_filenames: list, neg: str = NEG_DEFAULT,
                          width: int = 768, height: int = 1344, seed: int = 0,
                          steps: int = 20, cfg: float = 2.5) -> dict:
    """Qwen-Image-Edit-2509 多参考图（1-3张）→ 锁产品/角色外观一致性。"""
    refs = (ref_filenames or [])[:3]
    pos_inputs = {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt}
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": QWEN_MODELS["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": QWEN_MODELS["clip"], "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_MODELS["vae"]}},
        "10": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1}},
        "31": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": neg}},
        "40": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "50": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "positive": ["30", 0], "negative": ["31", 0], "latent_image": ["40", 0]}},
        "60": {"class_type": "VAEDecode", "inputs": {"samples": ["50", 0], "vae": ["3", 0]}},
        "70": {"class_type": "SaveImage", "inputs": {"images": ["60", 0], "filename_prefix": "aigc_qwen"}},
    }
    for i, fn in enumerate(refs):
        node_id = str(21 + i)
        g[node_id] = {"class_type": "LoadImage", "inputs": {"image": fn}}
        pos_inputs[f"image{i+1}"] = [node_id, 0]
    g["30"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": pos_inputs}
    return g


def build_flux_t2i_graph(prompt: str, width: int = 1024, height: int = 1024, seed: int = 0,
                         steps: int = 12, cfg: float = 5.0) -> dict:
    """Flux2 纯文生图（无参考图）。节点族两端通用；模型名来自当前档位；有 lora 则自动挂 Turbo 加速。"""
    model_src = ["70", 0]
    g = {
        "76": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": prompt}},
        "67": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["71", 0]}},
        "74": {"class_type": "CLIPTextEncode", "inputs": {"text": ["76", 0], "clip": ["71", 0]}},
        "70": {"class_type": "UNETLoader", "inputs": {"unet_name": FLUX_MODELS["unet"], "weight_dtype": "default"}},
        "71": {"class_type": "CLIPLoader", "inputs": {"clip_name": FLUX_MODELS["clip"], "type": "flux2", "device": "default"}},
        "72": {"class_type": "VAELoader", "inputs": {"vae_name": FLUX_MODELS["vae"]}},
        "68": {"class_type": "PrimitiveInt", "inputs": {"value": width}},
        "69": {"class_type": "PrimitiveInt", "inputs": {"value": height}},
        "61": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "62": {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": ["68", 0], "height": ["69", 0]}},
        "66": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": ["68", 0], "height": ["69", 0], "batch_size": 1}},
        "73": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "65": {"class_type": "VAEDecode", "inputs": {"samples": ["64", 0], "vae": ["72", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "aigc_flux", "images": ["65", 0]}},
    }
    # 对齐 aeon-spark 01 号流：Dev 档带 Turbo-LoRA 加速（仅模型侧）
    if FLUX_MODELS.get("lora"):
        g["75"] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"model": ["70", 0], "lora_name": FLUX_MODELS["lora"], "strength_model": 1.0}}
        model_src = ["75", 0]
    g["63"] = {"class_type": "CFGGuider", "inputs": {"cfg": cfg, "model": model_src, "positive": ["74", 0], "negative": ["67", 0]}}
    g["64"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": ["73", 0], "guider": ["63", 0], "sampler": ["61", 0], "sigmas": ["62", 0], "latent_image": ["66", 0]}}
    return g


# ---------- 提交 + 轮询 + 取图 ----------
def _post(path, body):
    req = urllib.request.Request(COMFY_URL + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def _get(path):
    with urllib.request.urlopen(COMFY_URL + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch_output_image(img: dict, out_dir: str) -> str:
    """把一张产出图落盘到 out_dir，返回本地路径。
    view 模式走 HTTP /view（跨机/Docker 通用）；fs 模式直接从 ComfyUI 输出目录拷（同机快路）。"""
    fn = img["filename"]
    sub = img.get("subfolder", "")
    dst = os.path.join(out_dir, f"comfyui_{int(time.time()*1000)}_{fn}")
    if COMFY_FETCH == "fs" and COMFY_OUTPUT:
        src = os.path.join(COMFY_OUTPUT, sub, fn)
        if os.path.isfile(src):
            shutil.copyfile(src, dst)
            return dst
        # 同机路径没读到 → 自动降级 HTTP /view
    q = urllib.parse.urlencode({"filename": fn, "subfolder": sub, "type": img.get("type", "output")})
    with urllib.request.urlopen(f"{COMFY_URL}/view?{q}", timeout=60) as r:
        data = r.read()
    with open(dst, "wb") as f:
        f.write(data)
    return dst


def submit_and_wait(graph: dict, out_dir: str, timeout: int = 600) -> dict:
    """提交工作流 → 轮询 → 落盘产出图，返回 {ok, image_paths, prompt_id, secs}。"""
    if not is_available():
        return {"ok": False, "msg": "ComfyUI 8188 未在线（启动 ComfyUI 后重试）"}
    r = _post("/prompt", {"prompt": graph})
    pid = r.get("prompt_id")
    if not pid:
        return {"ok": False, "msg": f"提交失败：{str(r)[:200]}"}
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            h = _get(f"/history/{pid}")
        except Exception:
            time.sleep(3); continue
        if pid in h:
            entry = h[pid]
            st = entry.get("status", {})
            if st.get("status_str") == "error":
                return {"ok": False, "msg": f"ComfyUI 执行错误：{json.dumps(st, ensure_ascii=False)[:300]}", "prompt_id": pid}
            paths = []
            for node_out in entry.get("outputs", {}).values():
                for img in node_out.get("images", []):
                    try:
                        paths.append(_fetch_output_image(img, out_dir))
                    except Exception as e:
                        return {"ok": False, "msg": f"取图失败({COMFY_FETCH}): {e}", "prompt_id": pid}
            if paths:
                return {"ok": True, "image_paths": paths, "prompt_id": pid, "secs": round(time.time() - t0, 1)}
        time.sleep(3)
    return {"ok": False, "msg": f"超时(>{timeout}s)", "prompt_id": pid}


# ---------- 预检：节点类型 + 模型文件是否真的在目标 ComfyUI 上 ----------
def preflight(mode: str = "both") -> dict:
    """用 /object_info 校验当前档位所需的节点类型与模型文件是否可用。
    mode: flux|qwen|both。返回 {ok, missing_nodes, missing_models, config}。"""
    try:
        info = _get("/object_info")
    except Exception as e:
        return {"ok": False, "msg": f"/object_info 拉取失败（ComfyUI 未在线？）: {e}"}

    need_nodes = set()
    need_models = []  # (loader_class, input_name, filename)
    if mode in ("flux", "both"):
        need_nodes |= {"UNETLoader", "CLIPLoader", "VAELoader", "Flux2Scheduler",
                       "EmptyFlux2LatentImage", "SamplerCustomAdvanced", "CFGGuider", "KSamplerSelect"}
        need_models += [("UNETLoader", "unet_name", FLUX_MODELS["unet"]),
                        ("CLIPLoader", "clip_name", FLUX_MODELS["clip"]),
                        ("VAELoader", "vae_name", FLUX_MODELS["vae"])]
        if FLUX_MODELS.get("lora"):
            need_nodes.add("LoraLoaderModelOnly")
            need_models.append(("LoraLoaderModelOnly", "lora_name", FLUX_MODELS["lora"]))
    if mode in ("qwen", "both"):
        need_nodes |= {"UNETLoader", "CLIPLoader", "VAELoader", "TextEncodeQwenImageEditPlus",
                       "ModelSamplingAuraFlow", "EmptySD3LatentImage", "KSampler", "LoadImage"}
        need_models += [("UNETLoader", "unet_name", QWEN_MODELS["unet"]),
                        ("CLIPLoader", "clip_name", QWEN_MODELS["clip"]),
                        ("VAELoader", "vae_name", QWEN_MODELS["vae"])]

    missing_nodes = sorted(n for n in need_nodes if n not in info)

    def _combo(cls, inp):
        node = info.get(cls, {})
        req = node.get("input", {}).get("required", {})
        opt = node.get("input", {}).get("optional", {})
        spec = req.get(inp) or opt.get(inp)
        # spec 形如 [["a.safetensors","b.safetensors"], {...}] —— 第一项是可选文件名列表
        if isinstance(spec, list) and spec and isinstance(spec[0], list):
            return set(spec[0])
        return None

    missing_models = []
    for cls, inp, fn in need_models:
        if cls in missing_nodes or not fn:
            continue
        combo = _combo(cls, inp)
        if combo is not None and fn not in combo:
            missing_models.append({"loader": cls, "name": fn})

    ok = not missing_nodes and not missing_models
    return {"ok": ok, "missing_nodes": missing_nodes, "missing_models": missing_models,
            "config": active_config()}


def generate(prompt: str, out_dir: str, ref_images: list = None, mode: str = "auto",
             width: int = 0, height: int = 0, seed: int = 0, neg: str = NEG_DEFAULT,
             timeout: int = 600) -> dict:
    """统一出图入口。
    mode=auto: 有 ref_images → qwen 多参考图锁一致性；无 → flux 文生图。"""
    if not is_available():
        return {"ok": False, "msg": "ComfyUI 8188 未在线"}
    refs = ref_images or []
    use_qwen = (mode == "qwen") or (mode == "auto" and refs)
    if use_qwen:
        try:
            ref_fns = [upload_image(p) for p in refs[:3]]
        except Exception as e:
            return {"ok": False, "msg": f"参考图上传失败：{e}"}
        w, h = (width or 768), (height or 1344)
        graph = build_qwen_edit_graph(prompt, ref_fns, neg=neg, width=w, height=h, seed=seed)
        provider = f"comfyui-qwen-edit({len(ref_fns)}参考图)@{COMFY_PROFILE}"
    else:
        w, h = (width or 1024), (height or 1024)
        graph = build_flux_t2i_graph(prompt, width=w, height=h, seed=seed)
        provider = f"comfyui-flux-t2i({_flux_key})@{COMFY_PROFILE}"
    res = submit_and_wait(graph, out_dir, timeout=timeout)
    res["provider"] = provider
    return res


if __name__ == "__main__":
    import sys
    print("=== active_config ===")
    print(json.dumps(active_config(), ensure_ascii=False, indent=2))
    print("ComfyUI available:", is_available(), "busy:", is_busy())
    if is_available():
        print("=== preflight ===")
        print(json.dumps(preflight("both"), ensure_ascii=False, indent=2))
        if "--gen" in sys.argv:
            out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "素材", "输出")
            r = generate("a minimalist celadon tea cup on dark background, product photography, photorealistic",
                         out, mode="flux", seed=42)
            print(json.dumps(r, ensure_ascii=False, indent=2))
