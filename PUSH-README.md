# 发布操作说明（README）

> 本文件指引你在**本机终端**完成推送到 GitHub 的操作。
> 说明：自动化沙箱环境无法出网，推送需在你的终端手动执行。

## 当前 git 状态（已就绪，等待推送）

| 项目 | 值 |
|------|-----|
| 仓库根目录 | `D:\lab\Agent\ai_agent` |
| 远程 | `https://github.com/alphyforest/accompAgent.git`（origin） |
| 主分支 | `main` @ `acc9bd6`（功能更新 + 发布说明，**不做发布**，继续桌面化） |
| 发布分支 | `release/web` @ `1d9b2ac`（Web 版发布线，锁住当前目录结构） |
| 版本 tag | `v0.2.0` @ `1d9b2ac`（Web 版首个发布版本） |

> `release/web` 与 `v0.2.0` 锚定在 Web 版代码快照 `1d9b2ac`；`main` 在此基础上额外多一个文档提交 `acc9bd6`。
> 自此各自演进：
> - `main`：桌面化、多引擎、目录重构，此后不打包发布
> - `release/web`：Web 版长期发布线，只发布这版结构

## 一、在本机终端执行推送（一次到位）

打开 PowerShell / CMD，进入项目目录并推送三者：

```powershell
cd D:\lab\Agent\ai_agent

# 1) 推送 main（当前已领先 origin，实际仅补上 1d9b2ac）
git push origin main

# 2) 推送发布分支 release/web
git push origin release/web

# 3) 推送版本 tag v0.2.0
git push origin v0.2.0
```

> 若 Git 弹出登录窗口，用 GitHub 账号登录，或粘贴令牌（见下文“令牌安全”）。
> 快捷方式：`git push origin main release/web v0.2.0` 可一次推送全部。

### 验证推送成功
```powershell
git fetch origin
git ls-remote --heads origin
git ls-remote --tags origin
```
应能看到 `refs/heads/main`、`refs/heads/release/web` 与 `refs/tags/v0.2.0`。

## 二、后续按需发布

- **只在发布分支上发布**：无需触碰 `main`。在 `release/web` 分支上改、提交、推送即可：
  ```powershell
  git checkout release/web
  # ...修改、git add、git commit...
  git push origin release/web
  ```
- **发新版本**：在 `release/web` 上打新 tag 并推送，例如：
  ```powershell
  git tag -a v0.2.1 -m "..."
  git push origin v0.2.1
  ```
- **打包产物（wheel / Docker 镜像）不入 git**：用 GitHub Releases 页面上传附件承载。

## 三、重要：令牌安全

- 你的 GitHub 令牌曾以明文存在于 `D:\lab\Agent\doc\git-access-token.txt`（不在 git 仓库内，**不会被推送**，但明文躺在磁盘上有风险）。
- **强烈建议**：本次推送完成后，前往 GitHub → Settings → Developer settings → Personal access tokens，将 `ghp_Eq5x...` 令牌 **Revoke（撤销）**，并按需重建。
- 之后避免在命令行明文粘贴令牌；改用「凭据管理器」或只读密钥存放。
