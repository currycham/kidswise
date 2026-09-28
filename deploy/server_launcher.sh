#!/usr/bin/env bash
# 服务器上的部署入口（装在 ~/waywise/bin/deploy，不在仓库目录里，避免 git checkout 时改到正在运行的自己）。
#
# 由 GitHub Actions 通过 SSH 调用：那把 SSH 密钥在 authorized_keys 里被限定为只能执行这个脚本，
# 客户端传来的命令（SSH_ORIGINAL_COMMAND）只被当作要部署的版本号（commit / tag / 分支）。
# 也可以在服务器上手动执行：~/waywise/bin/deploy <版本>
set -euo pipefail

BASE=/home/ubuntu/waywise
REPO=$BASE/repo
REF="${SSH_ORIGINAL_COMMAND:-${1:-origin/main}}"

if [[ ! "$REF" =~ ^[A-Za-z0-9._/-]{1,80}$ ]]; then
  echo "拒绝：版本号格式不对（$REF）" >&2
  exit 2
fi

exec 9>"$BASE/.deploy.lock"
if ! flock -n 9; then
  echo "拒绝：另一个部署正在进行" >&2
  exit 1
fi

cd "$REPO"
git fetch -q --prune origin '+refs/heads/*:refs/remotes/origin/*' '+refs/tags/*:refs/tags/*'
git checkout -q --detach "$REF"
echo "→ 部署 $(git log --oneline -1)"
exec bash "$REPO/deploy/server_deploy.sh"
