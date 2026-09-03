# 发布操作说明（README）

> 本文件指引你在**本机终端**完成推送到 GitHub 的操作。
> 说明：自动化沙箱环境无法出网，推送需在你的终端手动执行。

## 当前 git 状态

| 项目 | 值 |
|------|-----|
| 仓库根目录 | `D:\lab\Agent\ai_agent` |
| 远程 origin | `https://github.com/alphyforest/accompAgent.git`（开发主仓库） |
| 远程 public | `https://github.com/alphyforest/accompAgent-web.git`（**Web 发布库**） |
| 主分支 | `main`（R0~R6 完成，Stage R 收官；开发与发布同源） |
| 旧发布线 | `release/web`（历史 Web 版，锚定 `1d9b2ac`，tag `v0.2.0`） |
| **本次发布** | tag `v0.5.0`（R 完成版），待推 public |

> `release/web` 与 `v0.2.0` 锚定在 Web 版代码快照 `1d9b2ac`（历史线）；`main` 持续演进（桌面化、多引擎、目录重构）。
> **发布走 public（accompAgent-web）**；origin 只同步 main 与旧发布线。

## 〇、发布前自检

```powershell
cd D:\lab\Agent\ai_agent
python -m pytest -q --no-header    # 期望 200 passed
node --check static/app.js        # 前端语法
```

## 一、发布到 accompAgent-web（public）——本次 v0.5.0

public 的 main 当前是旧快照孤本（`d480cae`），本次用 main **全量覆盖**（旧孤本已在本地 `public-clean` 分支保留，可随时恢复）：

```powershell
cd D:\lab\Agent\ai_agent

# 1) 覆盖推送 main 到 public（--force 仅首次/覆盖发布需要；之后快进普通 push 即可）
git push public main:main --force

# 2) 推送发布 tag
git push public v0.5.0
```

### 验证推送成功
```powershell
git fetch public
git ls-remote --heads public
git ls-remote --tags public
```
应能看到 `refs/heads/main`（指向 `88d3188` 之后的发布 HEAD）与 `refs/tags/v0.5.0`。

## 二、origin 主仓库同步

```powershell
git push origin main
git push origin release/web
git push origin v0.2.0
```

## 三、后续按需发布

- **发新版本**：打新 tag 并推送 public：
  ```powershell
  git tag v0.6.0 -m "..."
  git push public v0.6.0
  ```
- **daily 发布**：main 快进后 `git push public main`（无需 force）。
- **打包产物（wheel / Docker 镜像）不入 git**：用 GitHub Releases 页面上传附件承载。

## 四、重要：令牌安全

- 避免在命令行明文粘贴令牌；改用「凭据管理器」或只读密钥存放。