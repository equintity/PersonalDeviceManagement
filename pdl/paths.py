"""应用路径体系:只读资源根 与 可写数据根 分离。

- 开发/源码模式:两者都指向仓库根目录(与历史行为一致)
- 桌面打包模式:resources = 安装目录(PyInstaller 资源包),
  data = %LOCALAPPDATA%\\PersonalDeviceManagement(或便携模式下的程序目录)

服务层参数既接受 Path(视为“资源+数据同根”),也接受 AppPaths ——
通过 as_resources()/as_data() 解析,保证两种模式无差别工作。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    resources: Path   # 只读:静态前端、固件模板、许可等
    data: Path        # 可写:SQLite 数据库、上传文件、运行日志


def as_resources(p) -> Path:
    """取资源根:AppPaths 取 .resources,Path 原样返回。"""
    res = getattr(p, "resources", None)
    return Path(res) if res is not None else Path(p)


def as_data(p) -> Path:
    """取数据根:AppPaths 取 .data,Path 原样返回。"""
    d = getattr(p, "data", None)
    return Path(d) if d is not None else Path(p)
