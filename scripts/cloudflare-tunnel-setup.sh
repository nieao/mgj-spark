#!/usr/bin/env bash
# ============================================================================
# mgj-spark · Cloudflare Tunnel 一键配置（DGX Spark / ARM64 / Ubuntu 24.04）
# ----------------------------------------------------------------------------
# 把 ComfyUI(8188) / AIGC工作台(8265) / mgj-spark面板(8250) 通过一条 Cloudflare
# 隧道暴露到公网三个子域名，只出站、免端口映射、自动 TLS。
#
# 前提：Cloudflare 账号里已托管本域名（DNS 在 Cloudflare）。
# 用法：
#   1. 改下面的 DOMAIN=你的域名
#   2. bash scripts/cloudflare-tunnel-setup.sh
#   3. 按提示完成一次浏览器授权（cloudflared tunnel login）
#   4. 去 Cloudflare Zero Trust 后台加 Access 登录门（脚本末尾打印步骤）
# ============================================================================
set -euo pipefail

# ======== 只需改这一行 ========
DOMAIN="nieao.eu.cc"                # ← 你的域名（DNS 须托管在 Cloudflare）
# ==============================

SUB_COMFY="comfy"                   # ComfyUI      → comfy.$DOMAIN
SUB_AIGC="aigc"                     # AIGC 工作台  → aigc.$DOMAIN
SUB_PANEL="spark"                   # mgj 面板     → spark.$DOMAIN
TUNNEL_NAME="mgj-spark"
CF_DIR="$HOME/.cloudflared"

say(){ printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die(){ printf '\n\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

[ "$DOMAIN" = "example.com" ] && die "请先把脚本里的 DOMAIN 改成你的真实域名再运行。"

# ---- 1. 安装 cloudflared（ARM64）----
if ! command -v cloudflared >/dev/null 2>&1; then
  say "安装 cloudflared (linux-arm64) ..."
  tmp="$(mktemp)"
  curl -fL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64" -o "$tmp"
  sudo install -m 0755 "$tmp" /usr/local/bin/cloudflared
  rm -f "$tmp"
fi
say "cloudflared 版本：$(cloudflared --version)"

# ---- 2. 授权登录（交互，会打印一个 URL 让你在浏览器点授权本域名）----
if [ ! -f "$CF_DIR/cert.pem" ]; then
  say "需要授权：下面会打印一个 URL，用你登录了 Cloudflare 的浏览器打开、选中 $DOMAIN 授权。"
  cloudflared tunnel login
fi
[ -f "$CF_DIR/cert.pem" ] || die "未检测到 $CF_DIR/cert.pem，授权未完成。"

# ---- 3. 创建隧道（幂等：已存在则复用）----
if cloudflared tunnel list 2>/dev/null | awk '{print $2}' | grep -qx "$TUNNEL_NAME"; then
  say "隧道 $TUNNEL_NAME 已存在，复用。"
else
  say "创建隧道 $TUNNEL_NAME ..."
  cloudflared tunnel create "$TUNNEL_NAME"
fi
TUNNEL_ID="$(cloudflared tunnel list | awk -v n="$TUNNEL_NAME" '$2==n{print $1}' | head -1)"
[ -n "$TUNNEL_ID" ] || die "拿不到 TUNNEL_ID。"
say "TUNNEL_ID = $TUNNEL_ID"

# ---- 4. 写 config.yml（三条 ingress）----
say "写 $CF_DIR/config.yml ..."
cat > "$CF_DIR/config.yml" <<EOF
tunnel: $TUNNEL_ID
credentials-file: $CF_DIR/$TUNNEL_ID.json
protocol: quic
no-autoupdate: true

ingress:
  - hostname: $SUB_COMFY.$DOMAIN
    service: http://127.0.0.1:8188
  - hostname: $SUB_AIGC.$DOMAIN
    service: http://127.0.0.1:8265
  - hostname: $SUB_PANEL.$DOMAIN
    service: http://127.0.0.1:8250
  - service: http_status:404
EOF

# ---- 5. 绑定三个子域名 DNS（幂等，已存在会提示可忽略）----
for h in "$SUB_COMFY" "$SUB_AIGC" "$SUB_PANEL"; do
  say "路由 DNS：$h.$DOMAIN → 隧道"
  cloudflared tunnel route dns "$TUNNEL_NAME" "$h.$DOMAIN" || \
    echo "  （$h.$DOMAIN 可能已存在解析，忽略）"
done

# ---- 6. 装成 systemd 服务（开机自启 + 崩溃自愈）----
say "安装 systemd 服务 ..."
sudo cloudflared --config "$CF_DIR/config.yml" service install || true
sudo systemctl enable --now cloudflared
sleep 2
sudo systemctl --no-pager status cloudflared | head -8 || true

cat <<EOF

============================================================================
 隧道已就绪。三个入口：
   https://$SUB_COMFY.$DOMAIN   → ComfyUI 8188   （现在就能通）
   https://$SUB_AIGC.$DOMAIN    → AIGC 工作台 8265（起了 8265 才通，否则 502）
   https://$SUB_PANEL.$DOMAIN   → mgj 面板 8250  （面板服务待开发，否则 502）

 ⚠ 最后一步（必须，在浏览器做）——给这三个域名加 Access 登录门，只放你邮箱：
   1. 打开 https://one.dash.cloudflare.com  → 选中你的账号
   2. Access → Applications → Add an application → Self-hosted
   3. Application domain 依次加：$SUB_COMFY.$DOMAIN / $SUB_AIGC.$DOMAIN / $SUB_PANEL.$DOMAIN
      （可一个应用配一个子域名，或用通配 *.$DOMAIN 一次覆盖）
   4. Policy：Action=Allow，Include → Emails → 填你的邮箱
   5. 保存。之后访问会先弹 Cloudflare 邮箱验证码，验过才进服务。

 没加 Access 之前，这三个地址等于把服务裸奔公网，尤其 ComfyUI 别裸奔。
============================================================================
EOF
