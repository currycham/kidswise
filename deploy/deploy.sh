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
# 语音识别用百炼（Qwen3-ASR-Flash）：优先 ASR_API_KEY，否则用 .env 里指向百炼的那组 LLM key
ASR_API_KEY="$(env_get ASR_API_KEY)"
if [[ -z "$ASR_API_KEY" ]]; then
  if [[ "$(env_get LLM_BASE_URL)" == *dashscope* ]]; then ASR_API_KEY="$(env_get LLM_API_KEY)"
  elif [[ "$(env_get LLM_FALLBACK_BASE_URL)" == *dashscope* ]]; then ASR_API_KEY="$(env_get LLM_FALLBACK_API_KEY)"
  fi
fi
if [[ -z "$ASR_API_KEY" ]]; then
  echo "请在 agent/.env 里填 ASR_API_KEY（阿里云百炼 API Key，用于语音识别）" >&2
  exit 1
fi
if [[ -z "$TTS_APPID" || -z "$TTS_ACCESS_TOKEN" ]]; then
  echo "请在 agent/.env 里填 TTS_APPID 和 TTS_ACCESS_TOKEN（豆包语音合成 2.0）" >&2
  exit 1
fi

echo "→ 同步 agent 到 $HOST"
rsync -az --delete -e "ssh -i $SSH_KEY" \
  --exclude .venv --exclude .pytest_cache --exclude __pycache__ \
  "$ROOT/agent/" "$HOST:waywise/agent/"
rsync -az -e "ssh -i $SSH_KEY" "$ROOT/deploy/docker-compose.yml" "$HOST:waywise/docker-compose.yml"
rsync -az -e "ssh -i $SSH_KEY" "$ROOT/deploy/xiaozhi/waywise-prompt.txt" "$HOST:waywise/xiaozhi/data/waywise-prompt.txt"

echo "→ 渲染 xiaozhi 配置"
sed -e "s|__PUBLIC_HOST__|$PUBLIC_HOST|g" -e "s|__AGENT_API_KEY__|$AGENT_API_KEY|g" \
  -e "s|__TTS_APPID__|$TTS_APPID|g" -e "s|__TTS_ACCESS_TOKEN__|$TTS_ACCESS_TOKEN|g" -e "s|__TTS_VOICE__|$TTS_VOICE|g" \
  -e "s|__ASR_API_KEY__|$ASR_API_KEY|g" \
  "$ROOT/deploy/xiaozhi/config.yaml.tmpl" |
  "${SSH[@]}" 'cat > waywise/xiaozhi/data/.config.yaml && chmod 600 waywise/xiaozhi/data/.config.yaml waywise/agent/.env'

echo "→ 更新开发者平台页面（waywise.tech/studio）"
rsync -az --delete -e "ssh -i $SSH_KEY" "$ROOT/studio/" "$HOST:waywise/studio/"
"${SSH[@]}" 'sudo mkdir -p /var/www/waywise-studio && sudo rsync -a --delete waywise/studio/ /var/www/waywise-studio/'

echo "→ 更新 Caddy（api.waywise.tech 的 HTTPS 入口）"
rsync -az -e "ssh -i $SSH_KEY" "$ROOT/deploy/caddy/Caddyfile" "$HOST:waywise/Caddyfile"
"${SSH[@]}" 'sudo cp waywise/Caddyfile /etc/caddy/Caddyfile && sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null && sudo systemctl reload caddy'

echo "→ 重建并启动"
"${SSH[@]}" 'cd waywise && sudo docker compose up -d --build && sudo docker compose restart xiaozhi && sudo docker compose ps'
