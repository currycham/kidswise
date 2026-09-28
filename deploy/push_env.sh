#!/usr/bin/env bash
# 把本机的 agent/.env（各种密钥）上传到服务器。密钥不进 git，只有这一条路进服务器。
# 上传后要重新部署才生效：deploy/deploy.sh
set -euo pipefail
HOST="${HOST:-ubuntu@111.229.194.119}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/waywise_ed25519}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT/agent/.env" ]] || { echo "缺少 agent/.env" >&2; exit 1; }
scp -q -i "$SSH_KEY" "$ROOT/agent/.env" "$HOST:waywise/agent/.env"
ssh -i "$SSH_KEY" "$HOST" 'chmod 600 waywise/agent/.env'
echo "✓ 已上传 agent/.env；运行 deploy/deploy.sh 让它生效"
