# 项目完成度快照（供看板对接 · 只读真相源）

- **生成**：2026-07-20，由**主开发会话**产出（下列条目均为本会话亲手实现 + 真机验证，或基线既有）
- **用途**：给看板 agent 把 `dgx_ck_*` / `dgx_task_*` 映射到**真实完成度**用的真相源
- **协作边界（不要冲突）**：本文件是主会话的**只读快照**；进度**时间线**与 `team.nieao.eu.cc/#board` **看板**由看板 agent 维护。**主会话不写时间线、不动看板**；看板 agent 只读本文件、不改本文件。各写各的文件，零并发冲突。
- 状态标记：`[✓]`=已完成并验证 · `[~]`=进行中/待一步 · `[ ]`=未开始

---

## mgj-spark（DGX Spark 飞书安全总控）

### 基线（既有，已完成）
- [✓] 安全内核：审批门 T0–T3 + Token 发证（工具白名单 + CIAA 审计）+ 分层记忆 L1/L2/L3（scope 强制隔离）+ 台账/成本估算
- [✓] 插件化架构（`plugins/` 自动发现 + can_handle 路由）：`spark_ops`(运维态只读) / `spark_gen`(出图) / `general_qa`(兜底)
- [✓] 飞书通道可切（http 纯 Python ws 接收 / larkcli）；**cli-spark 独立 bot**（与猫管家隔离）
- [✓] 观测看板 `observe/board.py`（8250，黑金只读）
- [✓] Cloudflare 隧道 + Basic Auth 反代（comfy/aigc/spark.nieao.eu.cc）
- [✓] systemd 守护 `mgj-spark.service`（active）

### 本会话新增
- [✓] **iterate 插件**（多步规划 + 对接故障建 GitHub Issue，**只规划不改码**）+ `core/github.py`
  - 已部署 Spark、mgj-spark 已重启、插件加载验证：`[spark_gen, iterate, spark_ops, general_qa]`
  - 规划模式端到端过 LLM 验证通过（出 7 步有依赖计划）
- [~] **issue 模式自动建单**：代码就绪，**Spark 待写入 `github_token`**
  - 细粒度 PAT `cli-spark-issues` 已生成（仅 nieao/spark 的 Issues 读写，2026-10-18 过期）
  - 卡点：token 待写入 `~/mgj-spark/config/credentials.json` + 重启 → 即闭环（当前唯一未闭环项）

---

## AIGC-spark（生图 / 视频工作台）

### 基线（既有，已完成）
- [✓] ComfyUI 生图客户端（Flux2 文生图 / Qwen-Edit 多参考图）+ `image_server.py` FastAPI 工作台（8265，systemd `aigc-spark.service`）

### 本会话新增（LTX 文生视频，全部部署 Spark + 真机验证）
- [✓] **LTX-2.3 22B 带音频文生视频链路**：复用 ComfyUI `/history` 黄金工作流 → `backend/ltx_video.py`
- [✓] **t2v + i2v 一致性流**（`workflows/ltx23_i2v_audio.json`，末帧→首帧 chain 续接）—— i2v 端到端验证出片（4.04s 带音频）
- [✓] **模板/项目层** `backend/ltx_project.py`：模板存盘 + 后台异步生成 + **单段重生成** + 合并
- [✓] **系统集成**：`image_server.py` 加 `/api/ltx/video/*` 7 端点 + `web/工作台/video.html` 黑金 UI —— systemd 重启、端点全验证（projects/status/merge/file/regenerate 实测通过）
- [✓] **实产交付**：猫黑客松 20 秒成片（5×4s，容器内 ffmpeg 合并）已出，发飞书 + 公开链接

---

## 跨项目
- [✓] **开源整合仓** `github.com/nieao/spark`（PUBLIC）：`control/`=mgj-spark 去密钥 + `aigc/`=LTX 视频链路子集；三重脱敏审计零密钥
- [✓] iterate 端到端验证建了 **Issue #1**（记录 Spark 待配 token 的待办）

---

## 给看板的映射建议
| 板块 | 完成度 |
|---|---|
| mgj-spark 安全内核 / 插件化 / 通道 / 观测看板 / 隧道 / systemd | 已完成 |
| iterate 多步规划 | 已完成 |
| iterate 故障自动建 Issue | **进行中**（待配 github_token，唯一未闭环项） |
| AIGC LTX 文生视频（生成 / i2v 一致性 / 模板 / 单段重生成 / 系统集成 / 交付） | 已完成 |
| 开源仓 nieao/spark | 已完成 |
