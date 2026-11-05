# 桌面版(Windows 独立运行)封装说明

本程序以 **PyInstaller(onedir)+ WebView2 原生窗口** 封装为 Windows 独立桌面应用:
双击 `PersonalDeviceManagement.exe` 即可运行,目标机器**无需安装 Python 或任何依赖**。
器件库管理全部功能(导入/手工维护/7×9 映射/联动/固件生成)与 Web 版完全一致,
界面即 Web 版图形界面,以原生桌面窗口呈现。

## 1. 整体形态

```
┌─────────────────────────────────────────────┐
│  PersonalDeviceManagement.exe(原生窗口)      │
│  └─ WebView2 渲染本地图形界面                  │
│      └─ 127.0.0.1:<随机端口>  ← Flask 服务线程 │
│          └─ SQLite(用户数据目录)              │
└─────────────────────────────────────────────┘
```

- **单进程应用**:主进程 = 桌面窗口(WebView2)+ 内嵌本地服务线程;
- 服务**仅绑定 127.0.0.1**,每次启动使用**随机端口**,不对外监听;
- 关闭窗口即退出;`--web` 参数可回退为"默认浏览器 + 心跳看门狗"模式
  (所有页面关闭后自动退出);无 WebView2 的系统自动走该回退;
- 两种数据模式:默认数据存 `%LOCALAPPDATA%\PersonalDeviceManagement`
  (安装目录只读);加 `--portable` 参数则存程序目录 `data\`(便携版,可 U 盘携带)。

## 2. 完整文件目录结构

### 2.1 交付产物(release/)

```
release/
├── PersonalDeviceManagement-v1.1.0-win64-portable.zip   # 便携包:解压即用
└── PersonalDeviceManagement-v1.1.0-win64-setup.exe      # 安装包(Inno Setup,按用户级安装,无需管理员)
```

### 2.2 安装后 / 解压后目录(onedir)

```
PersonalDeviceManagement/
├── PersonalDeviceManagement.exe        # 主程序(双击运行)
└── _internal/                          # 运行时与资源(PyInstaller onedir)
    ├── python314.dll 等                # 内嵌 Python 运行时(隔离,不依赖系统)
    ├── base_library.zip
    ├── pdl/static/                     # 前端资源(HTML/CSS/JS,只读)
    ├── firmware/template/              # ESP32 固件代码层模板(只读)
    ├── webview/ …                      # WebView2 互操作组件
    ├── LICENSE / README.md
    └── …(全部第三方依赖的二进制与库)
```

### 2.3 运行期生成的用户数据(默认 `%LOCALAPPDATA%\PersonalDeviceManagement\`)

```
PersonalDeviceManagement/               # %LOCALAPPDATA% 下
├── data/pdl.db(及 -wal/-shm)          # 器件库 SQLite 数据库
├── uploads/                            # 导入的订单文件副本(随机文件名)
└── (代码生成固件输出到用户自选目录,不写在本目录)
```

便携模式下以上内容位于 `程序目录\data\`;卸载安装包**不会删除**用户数据。

## 3. 第三方依赖的打包与隔离

| 层面 | 处理方式 |
|---|---|
| 运行时隔离 | PyInstaller onedir 把 CPython 3.14 解释器、全部纯 Python 依赖(Flask/Werkzeug/Jinja2、openpyxl、pyserial、pywebview/pythonnet)与二进制扩展**全部内嵌**到 `_internal\`,目标机器无需 Python、不受系统站点包影响、不会与其它 Python 冲突 |
| 构建隔离 | 构建脚本默认创建独立虚拟环境 `.venv-build` 并在其中安装依赖后打包(宿主环境零污染);实际使用的精确版本固化到 `requirements-lock.txt`,保证可复现构建 |
| 版本锁定 | `requirements.txt`(运行依赖,宽松下限)+ `requirements-lock.txt`(桌面构建锁定版)双层管理 |
| 资源只读 | 前端资源与固件模板打进安装目录,运行期只读;代码生成先在资源包中读取模板,再把产出写入用户指定的输出目录 |
| 不加壳 | 明确不使用 UPX 压缩/壳:降低杀软误报,保持产物可审计 |
| 加载边界 | 资源路径全部由启动器解析(`sys._MEIPASS`/安装目录),不接受环境变量覆盖;数据目录仅可通过 `--portable` 参数改变位置 |

## 4. 交付成品清单

1. **便携包** `*-portable.zip`:解压任意目录 → 双击 `PersonalDeviceManagement.exe`;
2. **安装包** `*-setup.exe`(Inno Setup):按用户级安装到
   `%LOCALAPPDATA%\Programs\PersonalDeviceManagement`,创建开始菜单/桌面快捷方式,
   卸载干净且保留用户数据;
3. **可执行程序本体** `dist\PersonalDeviceManagement\`(zip 与安装包的来源);
4. **配置与资源**:全部内嵌于产物(无外部配置文件);用户数据首次运行自动生成;
5. **文档**:本文件(封装与安全说明)、`README.md`(功能与使用)、
   `docs\SECURITY.md`(Web 层安全)、`docs\FIRMWARE.md`(固件与协议)、
   `docs\USAGE.md`(操作手册),均随包附带;
6. **源代码与构建脚本**(GitHub 仓库):`desktop/launcher.py`(启动器)、
   `PersonalDeviceManagement.spec`(打包配置)、`scripts/build_desktop.py`(一键构建)、
   `packaging/*.iss`(安装器脚本)、`scripts/make_icon.py`(图标)。

### 构建与发布流程

```bat
:: 前置:Windows 10/11 x64 + Python ≥3.10(3.14 已验证)
git clone https://github.com/equintity/PersonalDeviceManagement.git
cd PersonalDeviceManagement
python scripts\build_desktop.py
:: 产出 dist\ 与 release\;自检自动运行并必须通过才打包
:: (可选)安装 Inno Setup 6 后重新执行,将同时产出 setup.exe
```

发布:把 `release\` 下两个产物上传 GitHub Releases(Git 操作已有基线),
并在 Release 说明中附文件 SHA-256(构建脚本可扩展输出)。

## 5. 安全设计(独立运行 / 数据存储 / 依赖加载)

### 5.1 网络与接口

- 服务仅绑定 `127.0.0.1`,**每次启动随机端口**,外部机器无法连接;
- **API 守卫**(新增,Web/桌面同样生效):
  - Host 头必须是本机回环主机名 → 阻断 **DNS rebinding**;
  - 写请求(非 GET/HEAD/OPTIONS)要求同源:拒绝 `Sec-Fetch-Site: cross-site`
    与跨源 `Origin` → 阻断浏览器**drive-by CSRF**(其它网站无法操控你的器件库);
  - 只读请求不受影响,本地脚本(curl 等)不受影响;
- 生命周期端点(`system/shutdown`、`system/heartbeat`)受**每次启动随机令牌**保护:
  令牌仅注入给启动器拉起的本地页面(URL 参数,不下盘、不进日志),
  任何第三方网页即使能访问端口也无法关闭或保活服务;
- CSP 等安全响应头与 Web 版一致(禁内联脚本、禁外源、禁 iframe)。

### 5.2 数据存储

- 数据库/上传文件存于用户资料目录(`%LOCALAPPDATA%`),NTFS 默认 ACL
  仅允许本用户与管理员读取,其他标准用户不可见;
- 上传文件以**随机文件名**落盘(防路径注入),类型与大小双白名单(20MB/文件);
- SQL 全参数化;导出的 CSV 仅含用户数据;
- 卸载不删数据、重装不覆盖数据;便携模式数据自包含于程序目录,便于备份。

### 5.3 依赖与代码加载

- 依赖全部打包进 `_internal\`,运行期**不做任何在线安装/下载**;
  唯一出网行为是可选的“立创商城接口查询”(仅 GET、固定域名、10s 超时、0.4s 限速,
  失败静默降级,可在导入时取消勾选关闭);
- 资源加载路径固定为安装目录内部,无插件/动态导入机制,不存在加载不可信代码的入口;
- 生成的 ESP32 代码沿用电用安全链:白名单校验 → C 转义 → 生成后扫描(见 docs/SECURITY.md);
- 构建可复现:依赖版本锁定清单随仓库发布。

### 5.4 已知边界(诚实声明)

- 未做代码签名:首次运行可能触发 SmartScreen 提示(点“仍要运行”),
  杀软偶发误报 onedir 启动器 —— 官方渠道下载 + 校验 SHA-256 可规避仿冒包;
  有条件时建议用代码签名证书对 exe/安装包签名(脚本预留 spec 位置);
- 同机其他本地用户可访问本进程端口(本机信任边界内,数据 ACL 隔离);
- WebView2 运行时依赖系统组件(Win10 2004+/Win11 内置),极老系统自动回退浏览器模式;
- 立创接口为非官方公开端点,字段变化只会影响“联网补详情”,不影响核心功能。
