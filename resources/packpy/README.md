# 离线PYTHON环境安装包

## 目录结构

离线 Python 环境按目标平台拆分到子目录，目前提供 Windows 64 位。

```text
packpy/
├── README.md                        说明文档
└── win64/                           Windows x64 离线环境
    ├── .gitignore                   忽略 .venvs/、python-build-standalone/、wheels/
    ├── requirements.txt             离线安装所需的包列表
    ├── build.ps1                    Windows 准备脚本（开发机运行）
    ├── install.ps1                  Windows 安装脚本（部署机运行）
    ├── python-build-standalone/     存放下载的 standalone Python 发行包（压缩包与解压后的运行时）
    ├── wheels/                      pip download 产出的 wheel 包缓存
    │   ├── lxml-*.whl
    │   ├── pillow-*.whl
    │   ├── python_docx-*.whl
    │   ├── python_pptx-*.whl
    │   ├── typing_extensions-*.whl
    │   └── xlsxwriter-*.whl
    └── .venvs/                      install.ps1 生成的虚拟环境目录
        └── dev/                     用户脚本执行环境
            ├── Include/
            ├── Lib/site-packages/
            ├── Scripts/             含 python.exe / pip.exe / Activate.ps1 等
            └── pyvenv.cfg
```

> 说明：`.venvs/`、`python-build-standalone/` 与 `wheels/` 均已在 `win64/.gitignore` 中忽略，不会随仓库提交；它们分别由 `install.ps1` 与 `build.ps1` 生成或下载。后续若新增其他平台（如 `linux-x86_64/`、`macos-arm64/`），请按同样的结构在 `packpy/` 下建立独立子目录。

## 原理

**准备阶段**

* `requirements.txt` 中写明了要安装的包列表

* 下载目标系统对应的 `python-build-standalone`

* 使用 `pip download` 将需要的包下载到 `wheels` 文件夹

**迁移阶段**

* 利用 standalone Python 调用 `venv` 新建虚拟环境
* 激活虚拟环境后，直接 pip 安装包

## 使用方法

### win

**准备**（开发机）

运行 `packpy/win64/build.ps1`

**安装**（部署机）

运行 `packpy/win64/install.ps1`

**激活 Python 环境**

`./packpy/win64/.venvs/dev/Scripts/Activate` 则激活当前的 Python 环境
