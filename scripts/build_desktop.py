"""一键构建 Windows 桌面版:隔离 venv → PyInstaller → 自检 → 便携 zip。

用法:
    python scripts/build_desktop.py                # 完整隔离构建(推荐)
    python scripts/build_desktop.py --no-isolated  # 用当前解释器构建(开发快验)
    python scripts/build_desktop.py --skip-venv-install  # 复用已有 .venv-build

产出:
    dist/PersonalDeviceManagement/                       # 可直接双击运行
    release/PersonalDeviceManagement-v<版本>-win64-portable.zip
    release/...-setup.exe(检测到 Inno Setup 6 时额外生成)
"""

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
RELEASE = ROOT / "release"
APP_NAME = "PersonalDeviceManagement"

# 构建依赖与运行依赖分开声明;版本以构建脚本为准并写入锁定清单
RUNTIME_DEPS = ["flask", "openpyxl", "pyserial", "pywebview"]
BUILD_DEPS = ["pyinstaller"]


def app_version() -> str:
    sys.path.insert(0, str(ROOT))
    from pdl import APP_VERSION
    return APP_VERSION


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, cwd=ROOT, **kw)
    if r.returncode != 0:
        raise SystemExit(f"命令失败(rc={r.returncode}):{cmd}")


def make_venv(python: Path) -> Path:
    venv = BUILD / "venv-build"
    if not (venv / "pyvenv.cfg").exists():
        run([str(python), "-m", "venv", str(venv)])
    return venv


def venv_python(venv: Path) -> Path:
    return venv / "Scripts" / "python.exe"


def write_version_file(version: str) -> Path:
    """Windows EXE 版本资源(右键属性可见)。"""
    txt = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({version.split('.')[0]}, {version.split('.')[1]}, {version.split('.')[2]}, 0),
    prodvers=({version.split('.')[0]}, {version.split('.')[1]}, {version.split('.')[2]}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', 'equintity'),
        StringStruct('FileDescription', '个人器件库 PersonalDeviceManagement 桌面版'),
        StringStruct('FileVersion', '{version}.0'),
        StringStruct('InternalName', '{APP_NAME}'),
        StringStruct('LegalCopyright', 'GPL-3.0'),
        StringStruct('OriginalFilename', '{APP_NAME}.exe'),
        StringStruct('ProductName', '个人器件库 PersonalDeviceManagement'),
        StringStruct('ProductVersion', '{version}')])],
    VarFileInfo([VarStruct('Translation', [2052, 1200])]))])
"""
    BUILD.mkdir(exist_ok=True)
    path = BUILD / "exe_version.txt"
    path.write_text(txt, encoding="utf-8")
    return path


def pin_requirements(venv_python: Path) -> None:
    """把构建 venv 中实际安装的版本固化为 requirements-lock.txt(可复现构建)。"""
    out = subprocess.run([str(venv_python), "-m", "pip", "freeze", "--all"],
                         capture_output=True, text=True, check=True).stdout
    keep = []
    for line in out.splitlines():
        name = line.split("==")[0].lower()
        if name in ("flask", "openpyxl", "pyserial", "pywebview", "pyinstaller",
                    "werkzeug", "jinja2", "click", "itsdangerous",
                    "blinker", "markupsafe", "et-xmlfile", "pythonnet",
                    "clr-loader", "pyinstaller-hooks-contrib",
                    "packaging"):
            keep.append(line)
    (ROOT / "requirements-lock.txt").write_text(
        "# 由 scripts/build_desktop.py 生成:桌面版构建与打包的锁定版本\n"
        + "\n".join(sorted(keep)) + "\n", encoding="utf-8")
    print("已生成 requirements-lock.txt")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-isolated", action="store_true",
                    help="使用当前解释器构建(默认创建隔离 venv)")
    ap.add_argument("--skip-venv-install", action="store_true",
                    help="复用已有 .venv-build,跳过依赖安装")
    args = ap.parse_args()

    version = app_version()
    print(f"== 构建 {APP_NAME} 桌面版 v{version} ==")

    if args.no_isolated:
        build_python = Path(sys.executable)
        for dep in RUNTIME_DEPS + BUILD_DEPS:
            subprocess.run([str(build_python), "-c", f"import {dep}"],
                           check=True,
                           capture_output=True)
    else:
        venv = make_venv(Path(sys.executable))
        build_python = venv_python(venv)
        if not args.skip_venv_install:
            run([str(build_python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
            run([str(build_python), "-m", "pip", "install", "--quiet",
                 *RUNTIME_DEPS, *BUILD_DEPS])
        pin_requirements(build_python)

    version_file = write_version_file(version)
    run([str(build_python), "-m", "PyInstaller", "--noconfirm", "--clean",
         str(ROOT / f"{APP_NAME}.spec")], )

    # ---- 自检:运行 exe --selftest(无窗口,写报告) ----
    exe = DIST / APP_NAME / f"{APP_NAME}.exe"
    if not exe.exists():
        raise SystemExit(f"未找到产物:{exe}")
    report = BUILD / "selftest.log"
    print("== 运行自检 ==")
    r = subprocess.run([str(exe), "--selftest", "--report", str(report)],
                       timeout=120)
    if r.returncode != 0 or "SELFTEST PASS" not in report.read_text(encoding="utf-8"):
        raise SystemExit(f"自检失败,报告:{report}\n{report.read_text(encoding='utf-8')}")
    print(report.read_text(encoding="utf-8"))

    # ---- 便携 zip ----
    RELEASE.mkdir(exist_ok=True)
    zip_path = RELEASE / f"{APP_NAME}-v{version}-win64-portable.zip"
    if zip_path.exists():
        zip_path.unlink()
    base = DIST / APP_NAME
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in base.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(base.parent))
    print(f"便携包:{zip_path}({zip_path.stat().st_size / 1048576:.1f} MB)")

    # ---- Inno Setup 安装器(检测到 ISCC 才执行) ----
    for iscc in (Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
                 Path(r"C:\Program Files\Inno Setup 6\ISCC.exe")):
        if iscc.exists():
            run([str(iscc), f"/DAPP_VERSION={version}",
                 str(ROOT / "packaging" / f"{APP_NAME}.iss")])
            break
    else:
        print("未检测到 Inno Setup 6,跳过安装器;可安装后执行 packaging/*.iss 生成。")

    print("== 构建完成 ==")


if __name__ == "__main__":
    main()
