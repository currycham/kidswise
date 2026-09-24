#!/usr/bin/env bash
# 一键部署到云服务器：同步 agent 代码和配置，重建容器。
# 用法：deploy/deploy.sh            （默认 ubuntu@111.229.194.119）
#      HOST=ubuntu@x.x.x.x deploy/deploy.sh
set -euo pipefail

HOST="${HOST:-ubuntu@111.229.194.119}"
PUBLIC_HOST="${PUBLIC_HOST:-${HOST#*@}}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/waywise_ed25519}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH=(ssh -i "$SSH_KEY" "$HOST")

if [[ ! -f "$ROOT/agent/.env" ]]; then
  echo "缺少 agent/.env（从 agent/.env.example 复制一份再填）" >&2
  exit 1
fi
AGENT_API_KEY="$(grep -E '^AGENT_API_KEY=' "$ROOT/agent/.env" | cut -d= -f2-)"
if [[ -z "$AGENT_API_KEY" || "$AGENT_API_KEY" == "change-me" ]]; then
  echo "请先在 agent/.env 里把 AGENT_API_KEY 改成一个随机串" >&2
  exit 1
fi

env_get() { grep -E "^$1=" "$ROOT/agent/.env" | tail -1 | cut -d= -f2- || true; }
TTS_APPID="$(env_get TTS_APPID)"
TTS_ACCESS_TOKEN="$(env_get TTS_ACCESS_TOKEN)"
TTS_VOICE="$(env_get TTS_VOICE)"
TTS_VOICE="${TTS_VOICE:-zh_female_yingyujiaoxue_uranus_bigtts}"
if [[ -z "$TTS_APPID" || -z "$TTS_ACCESS_TOKEN" ]]; then
  echo "请在 agent/.env 里填 TTS_APPID 和 TTS_ACCESS_TOKEN（豆包语音合成 2.0）" >&2
  exit 1
fi

echo "→ 同步 agent 到 $HOST"
rsync -az --delete -e "ssh -i $SSH_KEY" \
  --exclude .venv --exclude .pytest_cache --exclude __pycache__ \
  "$ROOT/agent/" "$HOST:waywise/agent/"
rsync -az -e "ssh -i $SSH_KEY" "$ROOT/deploy/docker-compose.yml" "$HOST:waywise/docker-compose.yml"

echo "→ 渲染 xiaozhi 配置"
sed -e "s|__PUBLIC_HOST__|$PUBLIC_HOST|g" -e "s|__AGENT_API_KEY__|$AGENT_API_KEY|g" \
  -e "s|__TTS_APPID__|$TTS_APPID|g" -e "s|__TTS_ACCESS_TOKEN__|$TTS_ACCESS_TOKEN|g" -e "s|__TTS_VOICE__|$TTS_VOICE|g" \
  "$ROOT/deploy/xiaozhi/config.yaml.tmpl" |
  "${SSH[@]}" 'cat > waywise/xiaozhi/data/.config.yaml && chmod 600 waywise/xiaozhi/data/.config.yaml waywise/agent/.env'

echo "→ 重建并启动"
"${SSH[@]}" 'cd waywise && sudo docker compose up -d --build && sudo docker compose restart xiaozhi && sudo docker compose ps'
