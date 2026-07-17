# 会话记录 · 2026-07-16 · mgj-spark 落地 + Spark ComfyUI 跑通

> 一次会话完成：从零建 mgj-spark（猫管家 Spark 精简版）→ 上机部署守护 → 诊断修复并验证 DGX Spark 上的 aeon-spark ComfyUI 出图。
> 环境：DGX Spark `nvdspk02@73.237.141.115:2227`（GB10/ARM64/Ubuntu24.04/128G 统一内存），稳定通道 = Python paramiko。

---

## 一、需求与关键决策

**原始需求**：在 Spark 部署一个猫管家，管理「已部署一半的 `E:\claude code\AIGC-spark`」（ComfyUI 生图）；做个本地 `mgj-spark` 目录，清理并优化适配 Spark 系统与硬件。

**摸清的现状**：
- 猫管家原版 Windows 强绑定：飞书收发走 lark-cli(`%APPDATA%/npm/.../run.js`)、启动靠 `.bat/.vbs/PowerShell`、注册表 17 节点全是 `E:\` 路径、派发调本机 claude CLI。
- Spark 上真正要管的 = AIGC-spark 生图工作台(8265) + aeon-spark ComfyUI(8188) + spark-keeper 监控；AIGC-spark 才部署到「装 ComfyUI」这步。

**用户三决策**（AskUserQuestion 确认）：
1. **精简总控**——保留审批发证 + 分层记忆；**意图分类做成插件**（后续多项目可挂）。
2. **飞书通道可切**——http（纯 Python）/ larkcli（node）两种后端。
3. **大模型第三方**——默认 **StepFun 阶跃 `step-3.7-flash`**（OpenAI 兼容），key 只进 gitignore 的 credentials.json。

---

## 二、mgj-spark 构建（本地）

**关键工程判断**：飞书**发送本就是 HTTP**（tenant_access_token + REST），Node 只曾用于**接收** → http 通道用 `lark-oapi` 的 ws 长连接收消息，彻底去 Node，最适合无桌面 ARM 服务器。

**结构**：
- `core/`：registry/broker/approval/intent/memory_store/ledger/pricing/providers —— 从猫管家**原样搬**（扫描确认无 Windows 耦合，providers 的 CLI 路径 Linux 优雅降级）。providers 加 StepFun 设默认。
- `channel/`：`base.py`（工厂）+ `http_channel.py`（tenant_token 发送 + lark-oapi ws 接收）+ `larkcli_channel.py`（继承 http，只覆写 start_consume 走 node run.js）。
- `plugins/`：`base.py`（can_handle 最高分路由 + 自动发现）+ `spark_ops.py`（只读 T0 运维态卡）+ `general_qa.py`（intent 定档 + StepFun 单发 + 记忆注入）。
- `config/registry.json`（干净重写，无原版 GBK 乱码键）、`bridge.py`（精简编排：去重→本人校验→控制命令→硬阻断→插件路由→定档→审批门→发证→执行→台账）、`start.sh/stop.sh`、`systemd/mgj-spark.service`、`setup.py`、README/DEPLOY-SPARK.md。

**踩坑修掉**：spark_ops 触发词「部署」太贪婪，把 T2 动作请求抢去当只读、绕过审批门 → 改成「动作词存在且无状态信号则不接」，观测/动作分流。

**本地全绿自证**：selftest 通过、真调 StepFun 成功、审批放行闭环、硬阻断(rm -rf /)、路由分流均实测。

---

## 三、上机部署（Spark）

- 独立目录 `~/mgj-spark`（与 `~/spark-deploy`、docker `/workspace/ComfyUI` 完全隔离）；系统 python3 自带 requests，建独立 venv 装 `requests`+`lark-oapi`（只进 .venv，不碰系统/docker）。
- **systemd `mgj-spark.service` active + enabled（开机自启）**，飞书 http ws 长连接已连（`订阅 im.message.receive_v1`）。
- **非干扰实测（前后对比铁证）**：部署+跑 spark_ops、建 venv 装依赖、配 systemd 启动——三步全程隔壁 ComfyUI 下载正常推进（248→249→251GB）、下载进程存活。AIGC-spark 无飞书消费者 → 零双消费冲突。
- **发送闭环验证**：bot → 用户飞书发上线卡，用户确认收到。（接收半环需用户主动给 bot 发消息，bot 只认本人 open_id。）

---

## 四、诊断修复并验证 ComfyUI 出图（/goal）

**根因（逐层扒出，非猜测）**：容器 `comfyui-spark` `Up (unhealthy)`、8188 API 全空。entrypoint 逻辑 = 软链卷 → `download_models.py`(第111行) → `exec main.py`(第125行)；**下载卡在全量 321GB（LTX 视频 22B + ACE 音频，>7h 超时）永远到不了第125行，ComfyUI 从没启动**。ComfyUI 代码在 `/opt/ComfyUI/main.py`（`/workspace/ComfyUI` 是软链的持久卷）。

**修复（不动下载进程）**：Klein-9B 三件套模型早下完，用镜像自带 flags 从 `/opt/ComfyUI` 手动拉起，与下载并存、GPU 空闲：
```bash
sudo docker exec -d comfyui-spark bash -c \
 'cd /opt/ComfyUI && CUBLAS_WORKSPACE_CONFIG=:0:0 nohup /opt/venv/bin/python main.py \
  --listen 0.0.0.0 --port 8188 --use-sage-attention \
  --bf16-unet --bf16-vae --bf16-text-enc --disable-pinned-memory \
  --reserve-vram 2.0 --preview-method auto --enable-cors-header \
  > /workspace/comfyui_manual.log 2>&1 &'
```
15 秒起、15 节点全加载、`/object_info` 2.67MB。

**出图验证（真实证据）**：
- 环境：ComfyUI 0.20.1 · torch 2.9.1+cu130 · CUDA 13 · GB10 · 130G 统一内存 · SageAttention（FlashAttn 不支持 sm_121）
- 工作流：AIGC-spark `backend/comfyui_client.py:build_flux_t2i_graph` 同款 Klein-9B 图（UNETLoader `flux-2-klein-base-9b-fp8` / CLIPLoader `qwen_3_8b_fp8mixed` type=flux2 / VAELoader `full_encoder_small_decoder` / Flux2Scheduler / EmptyFlux2LatentImage / SamplerCustomAdvanced）
- preflight：缺节点 0 / 缺模型 0
- 提交中文 prompt「橘猫戴宇航头盔坐月面…」1024²/12步/cfg5/euler → `prompt_id 4c09cfd6…` → **36.0 秒 · 1,727,644 bytes · PNG 头校验通过 · 肉眼确认高质量写实、零花屏**
- mgj-spark spark_ops 已如实转绿：`🟢 ComfyUI cuda:0 NVIDIA GB10 · VRAM 122G`

---

## 五、诚实清单

| ✓ 本轮真实做到（带证据） | ○ 显式延后 |
|---|---|
| mgj-spark 全建成 + 本地全绿自证 | ○ ComfyUI 手动启动**不持久**（容器重启即失） |
| mgj-spark 上机 + systemd 守护 + ws 长连接 | ○ 全量视频/音频下载仍卡（HF xet 超时；生图不需要） |
| 飞书发送闭环验证（bot→user 卡片收到） | ○ AIGC-spark 工作台(8265) 未起（只验证引擎层） |
| ComfyUI 根因定位 + 修复 + Klein-9B 出图 | ○ Qwen-Image-Edit 多参考图仍是缺口（aeon 未预置） |
| 全程零扰动隔壁下载（前后进度对比） | ○ mgj-spark 已 git init 未 commit |

---

## 六、下一步（待用户确认执行；用户已说「先1后2」，因记录需求打断，尚未执行）

1. **让 ComfyUI 长期稳**：容器加 `SKIP_MODEL_DOWNLOAD=1` 重启 → entrypoint 直接 `exec main.py` 不再卡下载。**代价：会中断当前下载进程**，故需用户确认后再做。
2. **端到端出图工作台**：部署 AIGC-spark 后端到 Spark、`COMFY_PROFILE=aeon-spark FLUX_VARIANT=klein9b`、`./start.sh` 起 8265，测完整产品链路。

## 附：关键坐标
- mgj-spark 本地 `E:\claude code\mgj-spark`；Spark `~/mgj-spark`（systemd 守护）
- 出图留档 Spark `~/mgj-spark/out/klein_test_*.png`
- 修复档案 `E:\claude code\AIGC-spark\progress\06-comfyui-spark-launch-fix-verified.md`
- 工作流权威图 `AIGC-spark\backend\comfyui_client.py:build_flux_t2i_graph`
- 飞书通道：http（默认，lark-oapi ws）；大模型：StepFun `step-3.7-flash`
