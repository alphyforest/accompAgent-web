# AI 陪伴 Agent

一个基于 **FastAPI + Vue 3** 的 Python 全栈 AI 陪伴对话应用。角色拥有**情绪立绘**、**气氛值系统**、**事件触发**与**流式打字机输出**，可与用户进行带情绪的实时对话。

> 后端由 DeepSeek 大模型驱动，前端为免构建的 Vue 3（CDN 引入），开箱即用。

## 功能特性

- **流式对话**：SSE 流式输出，前端逐字打字机效果（含标点/换行停顿）
- **情绪立绘**：根据模型输出与气氛值动态切换角色立绘（思考中 / 喜怒哀乐 / 分享）
- **气氛值系统**：基于正/负关键词计分，随时间自然衰减，影响触发器与 prompt
- **角色卡（Character Card）**：人设、情绪标签、解析正则、立绘映射、主动触发器全部由 `character.json` 配置驱动，代码零硬编码
- **主动说话**：后台定时器按角色卡触发器规则，在用户静默一段时间后主动发起话题
- **事件系统（解耦中）**：链式状态机保留实现，暂不参与日常对话，作为"剧情模式"插件候选（`/api/event/trigger` 为调试口）
- **控制台**：立绘预览、背景切换、BGM 播放、聊天记录、设置
- **短期记忆**：滑动窗口对话历史
- **语义化长期记忆**（第二阶段）：跨会话记住用户画像/喜好/事实、延续话题、跟进约定（惰性总结 + LLM 结构化抽取 + top-k 注入 + 遗忘策略）

## 目录结构

```
ai_agent/
├── src/
│   ├── api/                # FastAPI 应用、路由、依赖注入、请求模型
│   │   ├── routes/         # chat / events / control 路由
│   │   ├── app.py          # 应用入口与静态资源挂载
│   │   └── dependencies.py # 单例依赖注入
│   ├── config/
│   │   ├── settings.py     # 配置（pydantic-settings，支持 .env）
│   │   └── roles/          # 角色配置：system_prompt.txt / events.json / phrases/
│   ├── core/
│   │   ├── agent/          # 对话引擎、事件系统、气氛值
│   │   ├── llm/            # DeepSeek 客户端与 prompt 构建
│   │   ├── memory/         # 短期记忆
│   │   └── character/      # 人设加载、语料
│   ├── mcp_gateway/        # MCP Server（预留）
│   └── utils/
├── static/                 # 前端（Vue3 + 原生 JS/CSS，免构建）
│   ├── index.html
│   ├── app.js / app.css
│   └── assets/             # 立绘、背景、BGM
├── phrases/                # 语料
├── tests/                  # pytest 单元测试
├── run.py                  # 统一入口（start / stop / mcp）
├── requirements.txt
└── pyproject.toml
```

## 环境要求

- Python >= 3.11
- 一个可用的 [DeepSeek](https://platform.deepseek.com) API Key

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

在项目根目录创建 `.env` 文件：

```env
DEEPSEEK_API_KEY=你的_api_key
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
# 推理深度：none（关闭思考，推荐）/ low / medium / high
REASONING_EFFORT=none
```

### 3. 启动服务

```bash
# 方式一：命令行入口
python run.py start

# 方式二：直接 uvicorn
uvicorn src.api.app:app --host 0.0.0.0 --port 5000
```

启动后访问 <http://localhost:5000/> 即可对话。

### 停止服务

```bash
python run.py stop
```

## 使用说明

### 对话

在输入框发送消息即可与角色对话。角色会：

- 根据你的情绪（关键词）更新气氛值
- 输出带情绪标签的回复，前端切换对应立绘
- 逐字打字机式展示回复

### 主动说话（主动触发器）

后台调度器周期性检查角色卡 `character.json` 的 `initiative_triggers`（气氛区间 + 概率 + 冷却 + 提示词）。当用户**静默超过阈值**且条件满足、未冷却时，Agent 会主动发一条消息（前端轮询 `/api/initiative` 展示）。新增/调整触发器只需改配置文件，重启即可生效。

> 冷书记录存内存（重启清零）。条件表达式支持简单 `eval`（如 `silence_seconds > 120`）。

### 事件系统（解耦中）

事件链式状态机保留实现但**不再由日常对话自动触发**（`invite`/`share` 测试密钥已移除），预留做"剧情模式"插件。可用 `/api/event/trigger` 手动按节点 id 触发以调试状态机。

> 事件配置见 `src/config/roles/events.json`。

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/chat/stream` | 流式对话（SSE） |
| `GET`  | `/api/mood` | 查询当前气氛值 |
| `GET`  | `/api/status` | 查询服务状态（气氛值/活跃链/冷却） |
| `GET`  | `/api/character` | 下发角色卡（立绘映射/默认情绪） |
| `GET`  | `/api/initiative` | 取出积压的主动发言（轮询） |
| `POST` | `/api/reset` | 三档清除并复位：`{"level": "session"\|"history"\|"all"}`（缺省 session） |
| `POST` | `/api/event/trigger` | 按节点 id 强制触发事件（调试口） |
| `GET`  | `/api/memory` | 列出全部长期记忆（按 category 分组） |
| `DELETE` | `/api/memory/{id}` | 单条删除记忆 |
| `POST` | `/api/memory/{id}/correct` | 纠正记忆：`{"value": "..."}`，并标记 `confirmed=1` |
| `GET`  | `/api/summaries` | 列出历史会话摘要 |

### 长期记忆说明（第二阶段）

- **机制**：对话累积到会话 buffer，空闲超时（默认 1h）或条数达阈值（默认 30 条）时，由 LLM 以 JSON 结构化抽取「会话摘要 + 用户画像事实」，落 SQLite（`user_memory` / `session_summaries` 表）。抽取失败自动降级，不阻塞对话。
- **注入**：每次对话将按 `importance` 取 top-k（默认 ≤8 条）记忆拼入 system prompt；`confirmed=0` 的条目会标注「推测」，不会当作事实向用户确认。
- **管理**：控制台「🧠 AI 记忆」页签可查看/确认/纠正/删除记忆；「📜 聊天记录」页签含历史会话摘要列表。
- **清除三档**：重置当前对话（session）/ 清除聊天记录（history，保留身份记忆）/ 忘记我（all，彻底清除）。
- **遗忘**：记忆被引用会刷新时间戳；超 30 天未引用降权 2 分，归零删除。

## 测试

```bash
pytest
```

## 配置说明

配置通过环境变量 / `.env` 注入（见 `src/config/settings.py`），主要项：

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `DEEPSEEK_API_KEY` | 必填 | DeepSeek API 密钥 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 模型名称 |
| `REASONING_EFFORT` | `low` | 推理深度（`none` 关闭思考可显著降低首字延迟） |
| `HOST` / `PORT` | `0.0.0.0` / `5000` | 服务监听地址 |
| `MAX_HISTORY` | `10` | 短期记忆滑动窗口大小 |
| `MEMORY_DB_PATH` | `./data/memory.db` | 长期记忆 SQLite 路径 |
| `MEMORY_IDLE_TIMEOUT_MINUTES` | `60` | 惰性总结空闲超时（分钟） |
| `MEMORY_SEGMENT_MAX_MESSAGES` | `30` | 惰性总结条数阈值 |
| `MEMORY_INJECT_TOP_K` | `8` | 记忆注入条数上限 |
| `MEMORY_FORGET_DAYS` / `MEMORY_FORGET_DECAY` | `30` / `2` | 遗忘策略参数 |

## 许可

[MIT](LICENSE)
