# NekoClaw

🐈 NekoClaw 是一个轻量级个人 AI Agent 框架，基于 [Nanobot](https://github.com/HKUDS/nanobot) 进行二次开发。
在保留 ReAct Agent 核心能力的基础上，补充了 Web UI（NekoChat）、多 IM 通道、Skills / MCP 工具系统、定时任务与 Heartbeat，并提供面向 Windows 的便携式离线打包方案（含 portable Chrome）。

---

## 目录

- [基本架构](#基本架构)
- [安装方式](#安装方式)
  - [方式一：本地 Python 开发环境](#方式一本地-python-开发环境)
  - [方式二：Windows 便携式离线包（含 portable Chrome）](#方式二windows-便携式离线包含-portable-chrome)
- [运行](#运行)
- [配置](#配置)

---

## 基本架构

NekoClaw 整体是一个事件驱动的 Agent Gateway：一个 `AgentLoop` 订阅来自多个 IM / Web 通道的消息，通过 LLM Provider 产出工具调用与响应，再由 `ChannelManager` 把流式结果广播回对应通道。

### 主要模块（`nekoclaw/`）


| 目录                        | 作用                                                                                              |
| ------------------------- | ----------------------------------------------------------------------------------------------- |
| `agent/`                  | ReAct 主循环 `AgentLoop`、`Subagent`、Memory、Skills 装配与 Context 组装                                   |
| `providers/`              | LLM 接入层（OpenAI 兼容），统一的 `StreamDelta` 流式协议                                                       |
| `bus/`                    | 进程内消息总线，连接通道 ↔ Agent（inbound / outbound events）                                                 |
| `channels/`               | IM / Web 通道适配：`nekochat`（Web UI）、`telegram`、`qq`、`dingtalk` 等                                   |
| `tools/` + `agent/tools/` | 工具集：`shell` / `terminal`、`filesystem`、`web`（Playwright）、`message`、`spawn`（子 Agent）、`cron`、`mcp` |
| `skills/`                 | 内置 Skill 包（`memory`、`cron`、`weather`、`github`、`tmux`、`skill-creator`），按需由 Agent 声明加载            |
| `session/`                | 会话与对话历史持久化（按 `channel:chat_id` 区分）                                                              |
| `cron/`                   | 定时任务服务，触发时经 `AgentLoop.process_direct` 走完整 Agent 流程                                             |
| `heartbeat/`              | 周期性心跳，用于后台自主任务与被动提醒                                                                             |
| `manager/`                | 运行时上下文、配置与 Skill 管理 API（被 NekoChat 前端调用）                                                        |
| `config/`                 | 配置加载 / 默认值 / 交互式初始化（`~/.nekoclaw/*.json`）                                                       |
| `templates/`              | 工作区模板（默认会同步到用户 workspace）                                                                       |


### 前端（`nekochat/`）

`nekochat/nekochat_frontend/` 是 Vite + TypeScript 的 Web UI 源码，`npm run build` 产出 `dist/` 静态页面；运行时由 Gateway 托管。

### Windows 打包资源（`resources/`）


| 目录                               | 作用                                                                                                             |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `resources/packpy/win64/`        | 离线 Python 环境：`python-build-standalone` + `wheels/`，`build.ps1` 准备、`install.ps1` 在目标机上创建 `main` / `dev` 两个 venv |
| `resources/chrome/chrome-win64/` | Portable Chrome，供 Playwright / Web 工具在离线环境下使用                                                                  |
| `scripts/win/build.ps1`          | 把源码、packpy、chrome、前端 `dist/` 打成 `NekoClaw-<version>-win64.zip`                                                 |
| `scripts/win/install.ps1`        | 在部署机上解压 zip、创建离线 venv、并交互式写入初始配置                                                                               |


---

## 安装方式

NekoClaw 提供两种安装路径：开发 / 自建环境推荐 **本地 Python**；面向非开发者用户或离线部署推荐 **Windows 便携式离线包**。

### 方式一：本地 Python 开发环境

适合开发、调试或在已有 Python 环境中运行。

**前置要求**

- Python ≥ 3.11
- Node.js ≥ 18（构建前端）
- 推荐使用 `conda` 或 `venv` 创建隔离环境

**安装步骤**

```bash
# 1. 克隆仓库
git clone https://github.com/<your-org>/NekoClaw.git
cd NekoClaw

# 2. 建立并激活 Python 虚拟环境（示例：venv）
python -m venv .venv
# Windows
.\.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

# 3. 安装 NekoClaw 及其依赖（以可编辑模式安装）
pip install -e .

# 5. 编译 NekoChat 前端静态页面
cd nekochat/nekochat_frontend
npm install
npm run build
cd ../..

```

**启动**

```bash
nekoclaw            # 等价于 `python -m nekoclaw`
# 常用参数
nekoclaw --workspace ~/.nekoclaw/workspace --verbose
```

### 方式二：Windows 便携式离线包（含 portable Chrome）

适合：部署机无网络 / 无 Python / 非开发者用户。最终产物是一个 zip + 一个 `install.ps1`，在目标机上双击解压即可运行。

#### A. 开发机：构建分发包

1. 准备离线 Python 环境（首次或依赖变化时）
  ```powershell
   cd resources\packpy\win64
   .\build.ps1
   # 产出：python-build-standalone/、wheels/
  ```
2. 放置 portable Chrome
  把便携版 Chrome 解压到 `resources\chrome\chrome-win64\`（即 `chrome.exe` 位于
   `resources\chrome\chrome-win64\chrome.exe`）。
3. 构建前端
  ```powershell
   cd nekochat\nekochat_frontend
   npm install   # 首次
   npm run build
   cd ..\..
  ```
4. 打包
  ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\win\build.ps1
  ```
   产出位于 `build/win/`：
   可选参数：`-KeepStaging` 保留中间目录、`-SkipArchive` 仅生成 staging。版本号读取自 `pyproject.toml`。

#### B. 部署机：一键安装

把 `install.ps1` 与 `NekoClaw-*.zip` 放在同一目录，执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本分三步：

1. **[1/3] 解压** zip 到 `-Destination`（默认为脚本所在目录），得到 `<Destination>\NekoClaw\`
2. **[2/3] 安装离线 Python 环境**：调用 `NekoClaw\resources\packpy\win64\install.ps1`，用 `python-build-standalone` 创建 `main`（运行时）与 `dev`（用户脚本执行）两个 venv，并从本地 `wheels/` 安装依赖
3. **[3/3] 交互式配置**：用 `main` venv 运行 `nekoclaw.config.loader.prompt_configs`，提示填入：
  - OpenAI API Key / Base URL / 默认模型
  - 模板语言（`en` / `cn`）
   结果写入 `~/.nekoclaw/config.json` 与 `~/.nekoclaw/providers.json`，已有值直接回车保留。

常用参数：


| 参数                    | 说明                               |
| --------------------- | -------------------------------- |
| `-Archive <path>`     | 显式指定 zip 路径                      |
| `-Destination <path>` | 解压目标目录（默认：脚本所在目录）                |
| `-Force`              | `<Destination>\NekoClaw` 已存在则先删除 |
| `-SkipPythonInstall`  | 只解压，不创建 venv（也会跳过配置）             |
| `-SkipConfigure`      | 创建 venv 但不进入交互式配置                |


#### C. 激活并运行

```powershell
. <Destination>\NekoClaw\resources\packpy\win64\.venvs\main\Scripts\Activate.ps1
nekoclaw --help
nekoclaw
```

Portable Chrome 路径为：

```text
<Destination>\NekoClaw\resources\chrome\chrome-win64\chrome.exe
```

需要时可在 `~/.nekoclaw/config.json` 中显式指向该可执行文件。

> 若只想重新配置，激活 `main` venv 后运行：
>
> ```powershell
> python -c "from nekoclaw.config.loader import prompt_configs; prompt_configs()"
> ```

---

## 运行

```bash
nekoclaw                                      # 直接启动
nekoclaw --workspace D:\nekoclaw\ws           # 指定工作区
nekoclaw --config path\to\config.json         # 指定配置文件
nekoclaw --verbose                            # 输出详细日志
```

启动后：

- Web UI（NekoChat）：默认 `http://127.0.0.1:8899/`，可在 `~/.nekoclaw/channels.json` 的 `nekochat.host` / `nekochat.port` 修改
- 其它 IM 通道（Telegram / QQ / DingTalk 等）按配置自动接入

---

## 配置

- 配置目录：`~/.nekoclaw/`
  - `config.json`：Gateway、Agent、工具、通道等
  - `providers.json`：OpenAI 等 Provider 凭据
  - `cron/jobs.json`：定时任务
- 工作区（workspace）默认在 `~/.nekoclaw/workspace/`，首次启动时会从 `nekoclaw/templates/` 同步模板
- 无 API Key 时 Gateway 仍可启动，可在 NekoChat 的「Config」面板中补填；首次请求前仅会打印一次警告

---

## License

MIT