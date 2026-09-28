# 部署

```
本地写代码 ──git push──▶ GitHub ──（手动触发）──▶ GitHub Actions
                                                ├─ 1. 跑全部测试，不通过就停
                                                └─ 2. SSH 到服务器，只传一个 commit 号
                                                       └─ 服务器：拉取这个 commit → 同步 → 重建容器 → 健康检查
```

| 做什么 | 怎么做 |
|---|---|
| 部署 main 最新提交 | `deploy/deploy.sh`，或 GitHub → Actions → deploy → Run workflow |
| 部署某个版本 | `deploy/deploy.sh v0.3`（标签 / 分支 / commit）；推送 `v*` 标签也会自动部署 |
| 改密钥（agent/.env） | 本机改好后 `deploy/push_env.sh`，再 `deploy/deploy.sh` |
| 看服务器上是哪个版本 | 服务器上 `cat ~/waywise/DEPLOYED` |
| 服务器上手动部署（应急） | `~/waywise/bin/deploy <版本>` |

每次 push 只会自动跑测试，**不会自动部署** —— 重启 xiaozhi 会打断孩子正在进行的对话。

## 密钥与权限

- **密钥只在服务器上**：`~/waywise/agent/.env` 不进 git，只能通过 `deploy/push_env.sh` 从本机上传
- **服务器拉代码**：服务器上 `~/.ssh/github_deploy`，是这个仓库的**只读**部署密钥（GitHub → Settings → Deploy keys）
- **GitHub Actions 登录服务器**：单独的一把密钥，存在仓库 Secrets（`DEPLOY_SSH_KEY / DEPLOY_HOST / DEPLOY_KNOWN_HOSTS`）。
  它在服务器的 `authorized_keys` 里被限定为只能执行 `~/waywise/bin/deploy`，不能开 shell、不能转发端口，
  传过去的内容只被当作版本号。要撤销：删掉 authorized_keys 里 `github-actions-deploy` 那一行

## 文件

| 文件 | 在哪跑 | 做什么 |
|---|---|---|
| `deploy.sh` | 本机 | 触发 GitHub Actions 部署并等待结果 |
| `push_env.sh` | 本机 | 上传 agent/.env |
| `server_launcher.sh` | 服务器（装在 `~/waywise/bin/deploy`） | 校验版本号、加锁、切到那个 commit |
| `server_deploy.sh` | 服务器 | 同步代码、渲染 xiaozhi 配置、更新 Caddy 与开发者平台页面、重建容器、健康检查 |
| `docker-compose.yml` | 服务器 | agent + xiaozhi |
| `xiaozhi/`、`caddy/` | 服务器 | xiaozhi 配置模板与补丁、HTTPS 入口 |
