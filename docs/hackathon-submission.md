# 猫管家 · Spark 安全总控 —— DGX Spark Hackathon 提交说明

> 一句话：**在一台 NVIDIA DGX Spark 上，用飞书当唯一入口，把「安全审批 + 大模型问答 + AIGC 生图/文生视频」拧成一个可对话、可运维、可外网访问的一体化智能体系统。**

## 一、作品概述

本作品是一套**跑在单台 NVIDIA DGX Spark（GB10 / ARM64 / 128GB 统一内存）上的本地 AIGC 智能体总控系统**，由三个协同模块组成：

- **mgj-spark（猫管家 · 安全总控）**：飞书里的一个 bot 即是整台机器的"遥控器"。用户在飞书发一句话，系统完成 *去重 → 本人校验 → 意图定档 → 危险硬阻断 → 插件路由 → 审批发证 → 执行 → 台账记账* 的全链路编排。
- **AIGC-spark（生图 / 文生视频工作台）**：基于 ComfyUI，跑 Flux2 文生图、Qwen-Image-Edit 多参考图编辑、以及 **LTX-2.3 22B 带音频文生视频**，直接吃满 GB10 的 128GB 统一内存。
- **观测 + 外网层**：黑金风只读观测看板 + Cloudflare 隧道（Basic Auth 登录门），把 Spark 上的三个服务安全暴露到公网，家宽 NAT 下零端口映射。

**要解决的痛点**：本地大算力机器（DGX Spark）通常只能 SSH 命令行操作，普通用户/团队无法安全便捷地驱动它出图、出视频、跑模型。本作品用飞书对话把这台机器变成"人人可安全指挥、但危险动作必须审批"的团队级 AIGC 生产力平台。

## 二、核心亮点

1. **安全内核是第一性设计**：审批门 T0–T3 分级（只读直跑 / 敏感需点"批准"才执行 / 灾难级请求如 `rm -rf /`、外发凭证直接拒绝受理）+ Token 发证（工具白名单 + CIAA 审计）+ 分层记忆 L1/L2/L3（scope 强制隔离）+ 全量台账与成本估算。**智能体有权力，但每一步权力都被门禁与审计约束**。
2. **插件化多能力融合**：`spark_ops`（运维态只读）/ `spark_gen`（出图）/ `iterate`（多步规划 + 故障自动建 GitHub Issue）/ `general_qa`（大模型兜底）四插件自动发现、按 `can_handle` 打分路由；只读插件绝不抢动作请求，动作一律回落安全门。**新接一个项目 = 多写一个插件文件**。
3. **充分吃满 DGX Spark 平台**：128GB 统一内存让 **LTX-2.3 22B（fp8）视频模型 + Gemma-3-12B 文本编码器 + Flux2** 能同机常驻共存；t2v + i2v 一致性流（末帧→首帧续接）已真机出片（带音频）。
4. **端到端可演示、真机验证**：全部模块已部署 Spark 并以 systemd 守护（开机自启 + 崩溃自愈）；观测看板 30+ 项自测全绿 + 浏览器 E2E 截图；已实产交付"猫黑客松"成片。

## 三、技术实现与架构设计

```
飞书用户 ──消息──> bridge.py（编排中枢）
                   去重→本人校验→控制命令→硬阻断→插件路由→定档→审批门→发证→执行→台账
                       │
      ┌────────────────┼─────────────────────┐
   core/ 安全内核    plugins/ 能力插件      channel/ 飞书通道（可切）
   registry 注册表   spark_ops 运维只读      http_channel（纯 Python·lark-oapi ws 长连接）
   broker Token发证  spark_gen  出图         larkcli_channel（node lark-cli）
   approval 审批门   iterate    多步规划+建Issue
   intent   定档     general_qa StepFun 兜底
   memory   分层记忆
   ledger/pricing 台账+成本

外网层：外部浏览器 → Cloudflare 边缘(自动TLS) → cloudflared 隧道(只出站)
        → Caddy 反代:9080 Basic Auth → { ComfyUI:8188 / AIGC工作台:8265 / 观测看板:8250 }
```

**关键设计决策**：

- **彻底去 Node、拥抱 ARM 服务器**：飞书发送本就是 HTTP（tenant_access_token + REST），接收改用 `lark-oapi` 的 WebSocket 长连接——纯 Python，无需桌面、无需 Node，最适合无头 ARM64 服务器。
- **模型层多 provider 可切**：默认 **StepFun step-3.7-flash**（OpenAI 兼容 HTTP，云端直连，零本地依赖）；飞书里一句"用 deepseek / 用 glm"即可临时切换。
- **只观测不接管**：mgj-spark 读 ComfyUI/AIGC 健康端点 + spark-keeper 部署进度 + `nvidia-smi`，与 AIGC-spark 同机共存、端口不冲突，各自 systemd 守护。

## 四、部署说明（本地算力如何部署智能体 · 如何优化大模型）

**智能体如何用本地算力部署**：

1. 代码经 scp/paramiko 送上 Spark，`python3 -m venv .venv && pip install -r requirements.txt`（纯 Python 依赖，ARM64 原生装）。
2. `python setup.py` 生成凭证模板，填飞书 app 三件套 + StepFun key。
3. `bridge.py --selftest` 全绿即就绪 → `systemctl enable --now mgj-spark` 交给 systemd 守护，WebSocket 断线自愈、进程崩溃自动重启。
4. AIGC-spark / ComfyUI 各自 systemd 守护，独立进程；Cloudflare 隧道 + Caddy 也各一 systemd 单元。**整机三层守护，全开机自启。**

**大模型如何在本地优化 / 适配 Spark**：

- **量化落地**：LTX-2.3 22B 采用 **fp8 权重 + distilled LoRA**，配合 GB10 的 128GB 统一内存，单机即可跑 22B 视频模型全链路（checkpoint + Gemma-3-12B 编码器 + VAE + 空间/时间放大器同时常驻）。
- **工作流复用而非硬拼**：ComfyUI 视频链路直接复用官方模板经 `/history` 捞黄金工作流拍平成 API 格式，避免盲拼节点反复烧 GPU。
- **分段一致性**：文生视频用 i2v 末帧→首帧 chain 续接 + 单段可重生成，长片按段生成再容器内 ffmpeg 合并，稳定可控。
- **对话侧走云端大模型**：意图分类 / 多步规划 / 兜底问答用 StepFun step-3.7-flash（低延迟、按量、免占本地显存），把宝贵的 GB10 算力全留给生图/视频扩散模型。

## 五、技术栈说明

| 层 | 使用的模型 / SDK / 工具 |
|---|---|
| **硬件平台** | NVIDIA DGX Spark（GB10 Grace Blackwell，aarch64，128GB 统一内存，Ubuntu 24.04） |
| **NVIDIA 平台能力** | CUDA on ARM（GB10 单卡驱动扩散模型）、ComfyUI 推理引擎跑在 GB10、`nvidia-smi` 运维观测 |
| **视觉 / 视频开源模型** | Flux2 文生图 · Qwen-Image-Edit 多参考图编辑 · **LTX-2.3 22B（fp8 + distilled LoRA）带音频文生视频** · Gemma-3-12B（LTX 文本编码器） |
| **StepFun 阶跃星辰** | **step-3.7-flash**（默认对话 / 意图定档 / iterate 多步规划大模型，OpenAI 兼容 HTTP） |
| **智能体 / 后端框架** | Python 3 · FastAPI（AIGC 工作台）· lark-oapi（飞书 WebSocket 长连接）· paramiko（稳定传输通道） |
| **运维 / 网络** | systemd（三层守护自愈）· Cloudflare Tunnel（cloudflared，家宽 NAT 零端口映射）· Caddy（Basic Auth 登录门） |

## 六、可访问入口 / 交付物

- 开源仓：`github.com/nieao/spark`（PUBLIC，含 `control/` 去密钥总控 + `aigc/` LTX 视频链路子集，三重脱敏审计零密钥）。
- 公网入口（Basic Auth 保护）：`comfy.nieao.eu.cc`（ComfyUI）/ `aigc.nieao.eu.cc`（生图工作台）/ `spark.nieao.eu.cc`（观测看板）。
- 实产交付：猫黑客松 20 秒成片（5×4s 带音频，容器内 ffmpeg 合并）。

---
*本文档为 DGX Spark Hackathon 第一次提交说明，可在飞书内多人协作编辑补充。*
