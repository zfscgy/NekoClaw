# NekoClaw

🐈 NekoClaw 是一个轻量级个人 AI Agent 框架。在一个 ReAct Agent 内核之上，叠加了
Web UI（NekoChat）、多 IM 通道、Skills / MCP 工具系统、定时任务与 Heartbeat，并
为 Windows 提供一套基于 **AutoCython + PyInstaller** 的离线打包方案（产物自带便携
Chrome 与独立 Python 运行环境，用户双击即可启动）。

---

## 目录

- [基本架构](#基本架构)
- [安装方式](#安装方式)
  - [方式一：本地 Python 开发环境](#方式一本地-python-开发环境)
  - [方式二：Windows 打包分发（AutoCython + PyInstaller）](#方式二windows-打包分发autocython--pyinstaller)
- [运行](#运行)
- [配置](#配置)

---

## 基本架构

NekoClaw 整体是一个事件驱动的 Agent Gateway：`AgentLoopDispatcher` 订阅来自多个
IM / Web 通道的消息，为每个会话起一个独立的 `AgentLoop`，通过 LLM Provider 产出
工具调用与响应，再由 `ChannelManager` 把流式结果广播回对应通道。

### 主要模块（`nekoclaw/`）

| 目录           | 作用                                                                                                                |
| -------------- | ------------------------------------------------------------------------------------------------------------------- |
| `agent/`       | ReAct 主循环 `AgentLoop`、`AgentLoopDispatcher`（按会话分发并并发执行）、`Subagent`、`Memory`、`Skills` 装配与 Context 组装 |
| `agent/tools/` | 内置工具集：`shell`、`filesystem`、`web`（Playwright）、`message`、`spawn`（子 Agent）、`cron`、`mcp`、`report`        |
| `providers/`   | LLM 接入层（OpenAI 兼容），统一的 `StreamDelta` 流式协议，自带 delta buffer 与转录支持                              |
| `bus/`         | 进程内消息总线，连接通道 ↔ Agent（inbound / outbound events）                                                       |
| `channels/`    | IM / Web 通道适配：`nekochat`（Web UI）、`telegram`、`qq`                                                            |
| `skills/`      | 内置 Skill 包（详见下方目录）；按需由 Agent 通过 `Skill` 工具声明加载                                               |
| `session/`     | 会话与对话历史持久化（按 `channel:chat_id` 区分）                                                                   |
| `cron/`        | 定时任务服务，触发时经 `AgentLoopDispatcher.process_direct` 走完整 Agent 流程                                       |
| `heartbeat/`   | 周期性心跳，用于后台自主任务与被动提醒                                                                              |
| `manager/`     | 运行时上下文、配置与 Skill 管理 API（被 NekoChat 前端调用）                                                         |
| `config/`      | 配置 schema / 加载 / 默认值 / 交互式初始化（位于 `~/.nekoclaw/*.json`）                                             |
| `startup/`     | 启动期任务：banner、日志、Chrome 探测、exec-tool venv 准备、可选 skill 同步                                         |
| `templates/`   | 工作区模板（首次启动会同步到用户 workspace，含 `en` / `cn` 两套）                                                   |
| `security/`    | 工具调用的权限校验与白名单                                                                                          |

### 内置 Skill

| 类型                    | Skill                                                                                                                    | 说明                                       |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------ |
| 内置（`skills/internal`） | `memory`、`cron`、`github`、`tmux`、`skill-creator`                                                                       | 主程序启动时自动可用                       |
| 可选（`skills/optional`） | `weather`、`news-reader`、`internet-trending`、`read-office-files`、`create-docx`、`create-pptx`                          | 首次启动同步到 workspace，可在 UI 中开关 |

可选 Skill 中的 `create-docx` / `create-pptx` 会通过 `exec` 工具调用 NekoClaw 自带
的 Python 环境（见下方「Windows 打包分发」），所以即便部署机上没有安装 Python，
也可以直接生成 Office 文件。

### Lightsear（`lightsear/`）

`lightsear/` 是 NekoClaw 内置的轻量级 Web 搜索 / 抓取库（基于 Playwright + 真实
Chrome via CDP），统一封装了 Google / Bing / DuckDuckGo / 百度，并向 `web` 工具
和 `news-reader`、`internet-trending` 等 Skill 提供能力。便携 Chrome 路径与代理等
配置都由 `tools.web` 控制。详情见 `docs/Lightsear.md`。

### 前端（`nekochat/`）

`nekochat/nekochat_frontend/` 是 Vite + TypeScript 的 Web UI 源码，`npm run build`
会产出 `dist/` 静态页面；运行时由 NekoChat 通道直接托管。

### Windows 打包资源（`resources/` + `build/`）

| 路径                             | 作用                                                                                                                          |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `resources/packpy/win64/`        | 离线 Python 环境：`python-build-standalone/` + 离线 wheels；首次启动时由 `startup/python_env.py` 在 `.venvs/dev/` 自动建好，供 `exec` 工具使用 |
| `resources/chrome/chrome-win64/` | 便携 Chrome，供 Playwright / Web 工具与 Lightsear 在离线环境下使用                                                            |
| `build/install.py`               | 打包脚本：先用 AutoCython 把 `nekoclaw` / `lightsear` 编译成 `.pyd`，再用 PyInstaller 把可执行文件、前端 `dist/`、packpy、Chrome 打成 one-folder 应用 |
| `build/main.py`                  | 打包后实际启动的 PyInstaller 入口（含依赖锚点和崩溃日志）                                                                     |

---

## 安装方式

NekoClaw 提供两种安装路径：开发 / 自建环境推荐 **本地 Python**；面向非开发者用户
或离线部署推荐 **Windows 打包分发**。

### 方式一：本地 Python 开发环境

适合开发、调试或在已有 Python 环境中运行。

**前置要求**

- Python ≥ 3.11
- Node.js ≥ 18（构建前端）
- 推荐使用 `conda` 或 `venv` 创建隔离环境
- 想用 Web 工具的话需要本机能跑 Chromium / Chrome（PyPI 上的 Playwright 会自动下载，或者用便携 Chrome 也行）

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

# 4. 编译 NekoChat 前端静态页面
cd nekochat/nekochat_frontend
npm install
npm run build
cd ../..
```

**启动**

```bash
nekoclaw                # 等价于 `python -m nekoclaw`
nekoclaw --workspace ~/.nekoclaw/workspace --verbose
```

首次启动 Neko 会提示填入 OpenAI Base URL / API Key / 默认模型与模板语言，并把结
果写到 `~/.nekoclaw/{config,providers,channels,tools}.json`（按 section 拆分的
四份文件）。

### 方式二：Windows 打包分发（AutoCython + PyInstaller）

适合：部署机没有 Python、没有网络、用户也不想关心环境的场景。最终产物是一个
`build/win/dist/NekoClaw/` one-folder 目录（含 `NekoClaw.exe` 与所有运行时资
源），打包后可以整体压缩分发，目标机解压即可运行。

#### A. 开发机：构建分发包

1. **准备便携 Chrome**

   把 Chrome / Chromium 的便携版解压到 `resources/chrome/chrome-win64/`，使得
   `resources\chrome\chrome-win64\chrome.exe` 存在。

2. **准备离线 Python 环境**（首次或依赖变化时）

   ```powershell
   cd resources\packpy\win64
   # 把 python-build-standalone 解压到 python-build-standalone\
   # 然后下载 exec 工具需要的 wheels（python-docx / python-pptx 等）：
   .\build.ps1
   ```

   产物：

   - `resources\packpy\win64\python-build-standalone\`（独立 Python 运行时）
   - `resources\packpy\win64\wheels\*.whl`（离线 wheel 缓存）

3. **安装打包工具链**

   在用于打包的 Python 环境里安装：

   ```powershell
   pip install pyinstaller
   pip install AutoCython-jianjun   # 提供 AutoCython 命令
   ```

4. **一键打包**

   ```powershell
   python build\install.py
   ```

   常用参数（可组合使用）：

   | 参数                       | 说明                                                                          |
   | -------------------------- | ----------------------------------------------------------------------------- |
   | `--clean`                  | 清理 `build/win/` 下的中间产物后再打包                                        |
   | `--skip-frontend-build`    | 跳过 `npm install` + `npm run build`，复用已有的 `nekochat_frontend/dist/`    |
   | `--skip-npm-install`       | 只跑 `npm run build`，不重新拉依赖                                            |
   | `--skip-cython`            | 跳过 AutoCython 编译，直接用上一次的 `build/win/cython-src/`                  |
   | `--keep-source`            | AutoCython 编译后保留 `.py` 源码（默认会替换为 `.pyd`）                       |
   | `--strict-resources`       | 缺少 packpy / chrome / 前端等资源时直接报错（默认只警告）                     |
   | `--windowed`               | 构建 GUI 版（无控制台）；默认 `--console` 方便看启动日志                      |
   | `--autocython-workers N`   | 透传给 AutoCython 的并发数                                                    |

   打包流程：

   1. `npm run build` 生成前端 `dist/`
   2. AutoCython 把 `nekoclaw/` 和 `lightsear/` 包编译为 `.pyd`（写入 `build/win/cython-src/`）；`__init__.py` / `__main__.py` 与 schema 等少量带特殊标记的文件会保持 `.py` 不参与编译
   3. 把 `resources/` 拷贝到 `build/win/resources/`（用作 PyInstaller 的 `--add-data` 源）
   4. PyInstaller 以 `build/main.py` 为入口生成 one-folder 应用，自动 collect 所有第三方依赖（aiohttp、httpx、openai、telegram、botpy、playwright、lxml、…）
   5. 把前端 `dist/`、`templates/`、`skills/`、`resources/` 一并打入产物

   最终产物：`build/win/dist/NekoClaw/NekoClaw.exe`（同目录是它的运行时依赖）。

#### B. 部署机：运行

把 `build/win/dist/NekoClaw/` 整个目录拷到目标机器即可。

```powershell
cd <NekoClaw 目录>
.\NekoClaw.exe
```

首次启动时：

- `startup/python_env.py` 会在 `resources\packpy\win64\.venvs\dev\` 下用 `python-build-standalone` 创建 exec 工具专用 venv，并从本地 `wheels\` 离线安装依赖
- 启动器会提示填入 OpenAI Base URL / API Key / 默认模型 / 模板语言，结果写到
  `~/.nekoclaw/`；之后再启动直接复用，不会再问
- 准备就绪后会自动打开 NekoChat 前端页面

崩溃信息会写到 `NekoClaw.exe` 同目录下的 `NekoClaw-crash.log`。

> 想完全重置配置：删除 `~/.nekoclaw/` 后重新启动即可重新走向导；或者激活 exec
> venv 后跑：
>
> ```powershell
> python -c "from nekoclaw.config.loader import prompt_configs; prompt_configs()"
> ```

---

## 运行

```bash
nekoclaw                                      # 直接启动
nekoclaw --workspace D:\nekoclaw\ws           # 指定工作区
nekoclaw --config path\to\config.json         # 指定配置文件（包含 sidecar 的目录）
nekoclaw --verbose                            # 输出详细日志
```

启动后：

- **NekoChat Web UI**：默认 `http://127.0.0.1:8899/`，可在 `~/.nekoclaw/channels.json` 的 `nekochat.host` / `nekochat.port` 修改
- **Telegram / QQ**：在 `channels.json` 中分别填入 Bot Token / AppID + Secret 并把 `enabled` 改为 `true` 后自动接入
- **Heartbeat**：默认每 30 分钟跑一次背景任务（可在 `config.json` 的 `gateway.heartbeat` 关闭或调整间隔）
- **Cron**：注册过的定时任务由 `cron/jobs.json` 持久化，触发时会复用 Agent 上下文跑一遍完整流程

---

## 配置

NekoClaw 的配置是 **「`config.json` + 三个 sidecar」** 的四文件结构，保存在
`~/.nekoclaw/` 下，每个文件只放一类设置：

| 文件             | 内容                                                                                 |
| ---------------- | ------------------------------------------------------------------------------------ |
| `config.json`    | `agents.defaults`（模型、温度、memory window 等）+ `gateway`（heartbeat）             |
| `providers.json` | `openai.api_key` / `api_base` / `extra_headers` / `recommended_models`               |
| `channels.json`  | `telegram` / `qq` / `nekochat` 三个通道的开关与凭据                                   |
| `tools.json`     | `tools.web`（Playwright / 便携 Chrome / Lightsear 搜索引擎开关）、`tools.exec`、MCP servers、`restrictToWorkspace` |

其它说明：

- 工作区（workspace）默认在 `~/.nekoclaw/workspace/`，首次启动时会按
  `agents.defaults.templateLocale`（`en` / `cn`）从 `nekoclaw/templates/` 同步模板
- 定时任务存到 `~/.nekoclaw/cron/jobs.json`，由 `CronService` 加载
- 日志在 `~/.nekoclaw/logs/`，媒体文件按通道分目录写到 `~/.nekoclaw/media/`
- 没填 API Key 时 Gateway 也能启动；可以稍后从 NekoChat 的「Config」面板补填，
  首次请求前只会打一次警告
- 任何 sidecar 缺失时会在保存时自动按当前内存中的值（或 schema 默认值）补齐

---

## License

MIT
