# 修复档案 · 缺口 A：ComfyUI 持久化（2026-07-16）

> 承接「mgj-spark × AIGC-spark × ComfyUI 契合度审计」。缺口 A 原判「ComfyUI 手动起不持久，容器重启即断」。
> 侦察后推翻原判 + 加固闭环，全程 paramiko 直连 Spark（`nvdspk02@73.237.141.115:2227`）。

## 侦察结论（推翻 progress/06 原判）

progress/06 的「`docker exec -d` 手动起、卡在 321GB 下载」是**更早的快照**。2026-07-16 实测现状：

| 检查项 | 实测 |
|---|---|
| ComfyUI :8188 | `200`，容器 `Up (healthy)` |
| main.py 进程 | PID 50 —— **entrypoint 原生 `exec`**，非 docker exec 手动起，带 `--enable-manager --enable-assets` |
| `.models_seeded` sentinel | **存在**（`workspace/.models_seeded`，7-15 18:43） |
| Klein 三件套 | 齐全：`flux-2-klein-base-9b-fp8`(9.5G) / `qwen_3_8b_fp8mixed`(8.6G) / `full_encoder_small_decoder`(250M) |
| restart 策略 | `restart: unless-stopped` |
| 当前下载进程 | 无（那个「会中断下载」的顾虑已不成立） |

**entrypoint.sh 第 5 段关键逻辑**（宿主 `~/comfyui-aeon-spark/entrypoint.sh`，`:ro` 挂进容器）：
```bash
SENTINEL="${WORKSPACE}/.models_seeded"
if [ "${SKIP_MODEL_DOWNLOAD:-0}" = "1" ]; then     日志跳过
elif [ -f "${SENTINEL}" ] && FORCE!=1; then         已 seed → 跳过
else 下载；成功 touch sentinel；失败也 warn 后继续 → 照样 exec main.py
```
即：sentinel 已在 → 重启本就跳过下载。progress/06 看到「卡住」是因为当时 download_models.py 还在同步跑没返回。

## 加固（确定性，不依赖 sentinel）

在 `~/comfyui-aeon-spark/.env` 追加 `SKIP_MODEL_DOWNLOAD=1`，让第 5 段第一条件短路，重启永不下载（生图只需已在的 Klein 三件套）。

- 备份：`.env.bak.mgj`（`cp -n`）。HF_TOKEN 完整（len=46）。
- compose 第 126 行 `SKIP_MODEL_DOWNLOAD: "${SKIP_MODEL_DOWNLOAD:-0}"` 原生支持。
- 生效：`docker compose up -d`（绝对路径 `/home/nvdspk02/comfyui-aeon-spark`；`sudo bash -lc` 里 `~` 会展开成 /root，需绝对路径）。

## 自证（真实证据）

1. compose 重建 → comfyui-spark Recreated → **25s** 回 `200 + healthy`。
2. `docker restart comfyui-spark`（持久化终极测试）→ entrypoint 日志确认：
   ```
   [entrypoint] SKIP_MODEL_DOWNLOAD=1 — skipping model fetch
   [entrypoint] Launching ComfyUI on port 8188
   [entrypoint] Flags: --listen 0.0.0.0 --port 8188 --use-sage-attention --fp8_e4m3fn-unet ...
   ```
   → **15s 回 200**，`/object_info` 2.67MB（15 节点全载，与 36s 出图基线一致）。
3. `restart: unless-stopped` → 重开机也自启。

## 回滚

```bash
cd /home/nvdspk02/comfyui-aeon-spark && cp .env.bak.mgj .env && sudo docker compose up -d
```

## 与 mgj-spark 的关系

无需改 mgj-spark 代码。spark_ops 会如实观测（🟢），spark_gen 出图链路终点（ComfyUI 8188）现在重启也稳。缺口 B（Qwen 参考图 mode 写死 flux + 模型未预置）、C（文档 klein9b 误导）、D（client 复制非共享）仍开放。
