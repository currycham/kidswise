#!/usr/bin/env bash
# 部署：让 GitHub Actions 先跑测试，通过后让服务器拉取这个版本并部署（见 .github/workflows/deploy.yml）。
#   deploy/deploy.sh            部署 main 的最新提交
#   deploy/deploy.sh v0.3       部署某个标签 / 分支 / commit
# 改了密钥（agent/.env）要先运行 deploy/push_env.sh。
set -euo pipefail
REF="${1:-main}"
cd "$(dirname "$0")/.."

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "提醒：本地有未提交的改动，它们不会被部署（部署的是 GitHub 上的 $REF）" >&2
fi
git fetch -q origin
if [[ "$REF" == "main" && "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
  echo "提醒：本地 main 和 GitHub 上的不一致，部署的是 GitHub 上的版本（先 git push）" >&2
fi

gh workflow run deploy.yml -f ref="$REF"
sleep 4
RUN=$(gh run list --workflow deploy.yml --limit 1 --json databaseId -q '.[0].databaseId')
echo "→ 部署任务：$(gh run view "$RUN" --json url -q .url)"
gh run watch "$RUN" --exit-status --interval 5
