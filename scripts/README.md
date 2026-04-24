# Scripts

本目录存放与源码无关的辅助脚本，按目标平台分子目录。

```text
scripts/
└── win/                Windows 打包 / 安装脚本
    ├── build.ps1
    └── install.ps1
```

## Windows 打包发布

`scripts/win/build.ps1` 负责将源码、离线 Python 环境、Chrome 运行时与前端产物打包成一个可分发的 zip。

### 前置条件

1. **离线 Python 环境**：先进入 `resources/packpy/win64` 运行 `build.ps1`，把 `python-build-standalone` 与 `wheels/` 准备好。
   ```powershell
   cd resources\packpy\win64
   .\build.ps1
   ```
2. **前端 dist**：`nekochat_frontend` 必须已经构建过。
   ```powershell
   cd nekochat\nekochat_frontend
   npm install   # 首次
   npm run build
   ```

### 执行打包

在仓库根目录下运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\win\build.ps1
```

脚本执行流程（对应 5 个阶段）：

1. 拷贝 `resources/packpy`（排除 `.venvs/`、`__pycache__/`）
2. 拷贝 `resources/chrome`（排除 `debug.log`）
3. 拷贝 `nekochat/nekochat_frontend/dist`
4. 拷贝 Python 源码：`nekoclaw/`、`lightsear/`（若存在）、`pyproject.toml`、`README.md`、`LICENSE`
5. 压缩为 `build/win/NekoClaw-<version>-win64.zip`，并把 `install.ps1` 复制到同目录

版本号自动从 `pyproject.toml` 的 `version` 字段读取。优先使用 Windows 自带的 `tar.exe`（对大目录如 `resources/chrome` 明显更快），不可用时回退到 `Compress-Archive`。

#### 参数

| 参数            | 说明                                                         |
| --------------- | ------------------------------------------------------------ |
| `-KeepStaging`  | 保留 `build/win/staging/` 中间目录，便于排查                 |
| `-SkipArchive`  | 仅生成 staging 目录，不产出 zip                              |

#### 产物

```text
build/win/
├── NekoClaw-<version>-win64.zip
└── install.ps1
```

将这两个文件一起发给部署机即可。

## Windows 部署安装

在部署机上把 `install.ps1` 和 `NekoClaw-*.zip` 放到同一目录，执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本会：

1. **[1/3] 解压**：自动找到同目录下的 `NekoClaw-*.zip`（可用 `-Archive <path>` 指定），解压到 `-Destination`（默认为脚本所在目录），得到 `<Destination>\NekoClaw\`
2. **[2/3] 安装 Python 环境**：调用 `NekoClaw\resources\packpy\win64\install.ps1`，用 `python-build-standalone` 创建 `main` / `dev` 两个虚拟环境，并从 `wheels/` 离线安装依赖
3. **[3/3] 交互式配置**：用 `main` venv 的 Python 运行 `nekoclaw.config.loader.prompt_configs`，提示用户填写：
   - OpenAI API Key (`openai_api_key`)
   - OpenAI Base URL (`openai_base_url`)
   - 默认模型 (`model`)
   - 模板语言 (`locale`，取值 `en` / `cn`)

   配置写入 `~/.nekoclaw/config.json` 与 `~/.nekoclaw/providers.json`；已有值会作为默认显示，直接回车即可保留。

> 若只想重新配置，可在激活 `main` venv 后运行：
> ```powershell
> python -c "from nekoclaw.config.loader import prompt_configs; prompt_configs()"
> ```

#### 参数

| 参数                   | 说明                                                         |
| ---------------------- | ------------------------------------------------------------ |
| `-Archive <path>`      | 显式指定 zip 路径                                            |
| `-Destination <path>`  | 解压目标目录（默认：脚本所在目录）                           |
| `-Force`               | 若 `<Destination>\NekoClaw` 已存在则先删除再解压             |
| `-SkipPythonInstall`   | 只解压，不安装 Python 环境（也会自动跳过配置提示）           |
| `-SkipConfigure`       | 安装 Python 环境但跳过最后一步的交互式配置                   |

#### 激活运行环境

```powershell
. <Destination>\NekoClaw\resources\packpy\win64\.venvs\main\Scripts\Activate.ps1
nekoclaw --help
```
