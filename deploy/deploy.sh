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

echo "→ 同步 agent 到 $HOST"
rsync -az --delete -e "ssh -i $SSH_KEY" \
  --exclude .venv --exclude .pytest_cache --exclude __pycache__ \
  "$ROOT/agent/" "$HOST:waywise/agent/"
rsync -az -e "ssh -i $SSH_KEY" "$ROOT/deploy/docker-compose.yml" "$HOST:waywise/docker-compose.yml"

echo "→ 渲染 xiaozhi 配置"
sed -e "s|__PUBLIC_HOST__|$PUBLIC_HOST|g" -e "s|__AGENT_API_KEY__|$AGENT_API_KEY|g" \
  "$ROOT/deploy/xiaozhi/config.yaml.tmpl" |
  "${SSH[@]}" 'cat > waywise/xiaozhi/data/.config.yaml && chmod 600 waywise/xiaozhi/data/.config.yaml waywise/agent/.env'

echo "→ 重建并启动"
"${SSH[@]}" 'cd waywise && sudo docker compose up -d --build && sudo docker compose ps'
