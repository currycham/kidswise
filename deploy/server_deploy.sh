#!/usr/bin/env bash
# 在服务器上按 ~/waywise/repo 当前的版本部署（由 server_launcher.sh 切好版本后调用）。
#
# 密钥只在服务器上：~/waywise/agent/.env（不进 git）。要改密钥：在本机改 agent/.env 后运行 deploy/push_env.sh。
# 目录布局见 deploy/docker-compose.yml 顶部注释。
set -euo pipefail

BASE=/home/ubuntu/waywise
REPO=$BASE/repo
ENV=$BASE/agent/.env
cd "$REPO"
SHA=$(git rev-parse --short HEAD)

[[ -f "$ENV" ]] || { echo "缺少 ${ENV}（先运行 deploy/push_env.sh）" >&2; exit 1; }
env_get() { grep -E "^$1=" "$ENV" | tail -1 | cut -d= -f2- || true; }

AGENT_API_KEY="$(env_get AGENT_API_KEY)"
TTS_APPID="$(env_get TTS_APPID)"
TTS_ACCESS_TOKEN="$(env_get TTS_ACCESS_TOKEN)"
TTS_VOICE="$(env_get TTS_VOICE)"; TTS_VOICE="${TTS_VOICE:-zh_female_yingyujiaoxue_uranus_bigtts}"
ASR_API_KEY="$(env_get ASR_API_KEY)"
if [[ -z "$ASR_API_KEY" ]]; then
  if [[ "$(env_get LLM_BASE_URL)" == *dashscope* ]]; then ASR_API_KEY="$(env_get LLM_API_KEY)"
  elif [[ "$(env_get LLM_FALLBACK_BASE_URL)" == *dashscope* ]]; then ASR_API_KEY="$(env_get LLM_FALLBACK_API_KEY)"
  fi
fi
for v in AGENT_API_KEY TTS_APPID TTS_ACCESS_TOKEN ASR_API_KEY; do
  [[ -n "${!v}" ]] || { echo "$ENV 里缺少 $v" >&2; exit 1; }
done
PUBLIC_HOST="$(env_get PUBLIC_HOST)"; PUBLIC_HOST="${PUBLIC_HOST:-111.229.194.119}"

echo "→ 同步代码与配置"
rsync -a --delete --exclude .env --exclude .venv --exclude .pytest_cache --exclude __pycache__ \
  "$REPO/agent/" "$BASE/agent/"
cp "$REPO/deploy/docker-compose.yml" "$BASE/docker-compose.yml"
mkdir -p "$BASE/xiaozhi/data" "$BASE/xiaozhi/patches" "$BASE/xiaozhi-image"
cp "$REPO/deploy/xiaozhi/waywise-prompt.txt" "$BASE/xiaozhi/data/waywise-prompt.txt"
rsync -a --delete "$REPO/deploy/xiaozhi/patches/" "$BASE/xiaozhi/patches/"
rsync -a --delete "$REPO/deploy/xiaozhi/image/" "$BASE/xiaozhi-image/"
sed -e "s|__PUBLIC_HOST__|$PUBLIC_HOST|g" -e "s|__AGENT_API_KEY__|$AGENT_API_KEY|g" \
  -e "s|__TTS_APPID__|$TTS_APPID|g" -e "s|__TTS_ACCESS_TOKEN__|$TTS_ACCESS_TOKEN|g" -e "s|__TTS_VOICE__|$TTS_VOICE|g" \
  -e "s|__ASR_API_KEY__|$ASR_API_KEY|g" \
  "$REPO/deploy/xiaozhi/config.yaml.tmpl" > "$BASE/xiaozhi/data/.config.yaml"
chmod 600 "$BASE/xiaozhi/data/.config.yaml" "$ENV"

echo "→ 开发者平台页面与 HTTPS 入口"
sudo mkdir -p /var/www/waywise-studio
sudo rsync -a --delete "$REPO/studio/" /var/www/waywise-studio/
# 主 /etc/caddy/Caddyfile 只是各产品 *.caddy 的 import 列表，归 waywise_project/ops/caddy 管；这里只装自己的 kidswise.caddy
TS=$(date +%Y%m%d%H%M%S)
sudo cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.$TS"
[[ -f /etc/caddy/kidswise.caddy ]] && sudo cp /etc/caddy/kidswise.caddy "/etc/caddy/kidswise.caddy.bak.$TS"
sudo install -m 644 "$REPO/deploy/caddy/kidswise.caddy" /etc/caddy/kidswise.caddy
sudo grep -q '^import kidswise.caddy' /etc/caddy/Caddyfile \
  || printf '\nimport kidswise.caddy\n' | sudo tee -a /etc/caddy/Caddyfile >/dev/null
if ! sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null; then
  sudo cp "/etc/caddy/Caddyfile.bak.$TS" /etc/caddy/Caddyfile
  if [[ -f "/etc/caddy/kidswise.caddy.bak.$TS" ]]; then sudo cp "/etc/caddy/kidswise.caddy.bak.$TS" /etc/caddy/kidswise.caddy; else sudo rm -f /etc/caddy/kidswise.caddy; fi
  echo "Caddy 配置校验失败，已还原。若主 Caddyfile 里还有旧的 api.waywise.tech 配置，先运行 waywise_project/ops/caddy/apply.sh" >&2
  exit 1
fi
sudo systemctl reload caddy

echo "→ 重建并启动容器"
cd "$BASE"
sudo docker compose up -d --build --quiet-pull 2>&1 | grep -vE "^\s*$" | tail -5
sudo docker compose restart xiaozhi >/dev/null  # xiaozhi 只在启动时读配置

echo "→ 健康检查"
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:8080/healthz >/dev/null && break
  sleep 2
  [[ $i == 30 ]] && { echo "agent 没有起来" >&2; sudo docker compose logs --tail 30 agent >&2; exit 1; }
done
for i in $(seq 1 45); do
  sudo docker logs --since 2m xiaozhi-esp32-server 2>&1 | grep -q "Websocket地址" && break
  sleep 2
  [[ $i == 45 ]] && { echo "xiaozhi 没有起来" >&2; sudo docker logs --tail 30 xiaozhi-esp32-server >&2; exit 1; }
done
curl -sf https://api.waywise.tech/healthz >/dev/null || { echo "api.waywise.tech 访问不到" >&2; exit 1; }

# 顺带更新部署入口（入口早已 exec 到本脚本，这里改它是安全的）
mkdir -p "$BASE/bin" && install -m 755 "$REPO/deploy/server_launcher.sh" "$BASE/bin/deploy"

echo "$SHA $(date '+%F %T')" > "$BASE/DEPLOYED"
echo "✓ 已部署 ${SHA}：agent、xiaozhi、api.waywise.tech 都正常"
