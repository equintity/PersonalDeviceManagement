"""PersonalDeviceManagement 桌面启动器。

形态:
- 默认:WebView2 原生窗口(pywebview),关闭窗口即退出应用;
- --web:回退为系统默认浏览器 + 心跳看门狗(所有页面关闭后自动退出);
- --selftest:无界面自检(启动服务 → 校验核心接口 → 写报告 → 退出),
  供打包流水线验证,不弹窗口。

路径安全:
- 资源根:冻结运行取 PyInstaller 资源包(sys._MEIPASS),源码运行取仓库根;
- 数据根:默认 %LOCALAPPDATA%\\PersonalDeviceManagement(安装目录保持只读,
  即便安装到 Program Files 也可正常写数据);--portable 时改为程序目录\\data;
- 每次启动随机端口 + 随机生命周期令牌,令牌只出现在本机启动的页面 URL 中。
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "PersonalDeviceManagement"
APP_TITLE = "个人器件库 PersonalDeviceManagement"
HEARTBEAT_TIMEOUT = 10.0   # 秒:超过该时长无心跳且处于浏览器模式 → 退出
START_TIMEOUT = 20.0


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))           # PyInstaller onedir 资源
    return Path(__file__).resolve().parent.parent       # 源码运行:仓库根


def data_root(portable: bool) -> Path:
    if portable:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
            else resource_root()
        return base / "data"
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _get(url: str, timeout: float = 2.0):
    req = urllib.request.Request(url, headers={"User-Agent": "PDL-Launcher/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def wait_until_ready(port: int, token: str | None = None) -> bool:
    deadline = time.time() + START_TIMEOUT
    url = f"http://127.0.0.1:{port}/api/system/info"
    while time.time() < deadline:
        try:
            status, _ = _get(url)
            if status == 200:
                return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.25)
    return False


def start_server(port: int, data: Path, token: str):
    """在守护线程中启动 Flask;进程主线程退出时一并结束。"""
    from pdl import create_app

    app = create_app(resource_root(), data_root=data,
                     port=port, shutdown_token=token)
    t = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port,
                               threaded=True, debug=False, use_reloader=False),
        name="pdl-server", daemon=True)
    t.start()
    return app


def selftest(port: int, data: Path, report: Path) -> int:
    """无界面自检:启动 → 校验接口与冻结环境关键路径 → 报告 → 退出。

    始终使用独立临时数据目录,不触碰真实用户数据。
    """
    import json
    import tempfile

    token = os.urandom(16).hex()
    data = Path(tempfile.mkdtemp(prefix="pdl-selftest-"))
    lines: list[str] = []
    ok = True

    def api(path: str, method: str = "GET", payload=None, expect=200):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method, data=body,
            headers={"Content-Type": "application/json",
                     "User-Agent": "PDL-Launcher/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    try:
        app = start_server(port, data, token)
        if not wait_until_ready(port):
            raise RuntimeError("服务未在时限内就绪")
        status, body = _get(f"http://127.0.0.1:{port}/api/system/info")
        assert status == 200 and '"ok"' in body, "system/info 异常"
        lines.append(f"system/info OK(db={json.loads(body)['data']['db_path']})")
        status, body = _get(f"http://127.0.0.1:{port}/")
        assert status == 200 and "个人器件库" in body, "首页/静态资源异常"
        lines.append("index OK")

        # 写链路:建器件 → 映射 → 生成固件(验证固件模板资源已打包)
        status, body = api("/api/devices", "POST",
                           {"name": "SELFTEST-DEVICE", "qty": 1})
        assert status == 201, f"建器件异常:{body[:120]}"
        device_id = json.loads(body)["data"]["id"]
        status, body = api("/api/box/mapping", "POST",
                           {"cell": 0, "device_id": device_id})
        assert status == 200, f"映射异常:{body[:120]}"
        out_dir = Path(tempfile.mkdtemp(prefix="pdl-selftest-fw-"))
        status, body = api("/api/codegen/generate", "POST",
                           {"output_dir": str(out_dir)})
        assert status == 200, f"代码生成异常:{body[:200]}"
        assert (out_dir / "include" / "box_config.h").exists(), "固件头未生成"
        assert (out_dir / "platformio.ini").exists(), "platformio.ini 未复制"
        lines.append("device + mapping + codegen OK(冻结环境资源完整)")
        # 生命周期端点必须因令牌校验而拒绝错误令牌
        status, _ = api("/api/system/shutdown", "POST", None)  # 无令牌
        # 注:api() 不带令牌,应 403
        assert status == 403, "shutdown 未校验令牌!"
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/system/shutdown", method="POST",
            headers={"X-PDL-Token": "wrong"})
        try:
            urllib.request.urlopen(req, timeout=2)
            raise RuntimeError("shutdown 未校验令牌!")
        except urllib.error.HTTPError as e:
            assert e.code == 403, "shutdown 令牌校验异常"
        lines.append("shutdown token guard OK")
        lines.append("SELFTEST PASS")
    except Exception as exc:  # noqa: BLE001
        ok = False
        lines.append(f"SELFTEST FAIL: {exc}")
    finally:
        lines.append(f"port={port} data={data}")
        report.write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
    return 0 if ok else 1


def run_browser_mode(port: int, token: str, app) -> None:
    """浏览器模式:打开页面,心跳看门狗守护。

    - 收到首个心跳前给 120s 宽限(等待浏览器拉起页面);
    - 心跳开始后,超过 10s 无任何页面心跳 → 视为全部页面已关闭 → 退出;
    - 收到 shutdown 指令(页面“退出程序”按钮)→ 立即退出。
    """
    webbrowser.open(f"http://127.0.0.1:{port}/?shtoken={token}")
    from pdl.api import _ctx
    launched_at = time.time()
    ever_heartbeat = False
    print(f"{APP_TITLE} 已在浏览器打开(http://127.0.0.1:{port}/),"
          f"关闭所有页面后程序自动退出。")
    while True:
        evt = _ctx.get("shutdown_event")
        if evt is not None and evt.is_set():
            print("收到退出指令,正在关闭…")
            return
        last = float(_ctx.get("last_heartbeat", time.time()))
        if last > launched_at + 1:            # 启动后有心跳写入
            ever_heartbeat = True
        if ever_heartbeat:
            if time.time() - last > HEARTBEAT_TIMEOUT:
                print("检测到所有页面已关闭,正在退出…")
                return
        elif time.time() - launched_at > 120:
            print("长时间未检测到页面心跳,正在退出…")
            return
        time.sleep(1.0)


def run_window_mode(port: int, app) -> int:
    """原生窗口模式(WebView2)。pywebview 不可用时自动回退浏览器模式。"""
    try:
        import webview  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"原生窗口组件不可用({exc.__class__.__name__}),回退浏览器模式。")
        run_browser_mode(port, app.config.get("PDL_TOKEN", ""), app)
        return 0
    from pdl.api import _ctx
    window = webview.create_window(
        APP_TITLE, f"http://127.0.0.1:{port}/",
        width=1280, height=850, min_size=(1000, 640),
        background_color="#0f141a")
    webview.start(gui="edgechromium")   # 阻塞至窗口关闭
    _ctx.get("shutdown_event").set()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE + " 桌面版")
    parser.add_argument("--port", type=int, default=0, help="固定端口(默认随机)")
    parser.add_argument("--portable", action="store_true",
                        help="便携模式:数据存于程序目录 data\\ 而非用户目录")
    parser.add_argument("--web", action="store_true",
                        help="使用默认浏览器打开(不创建原生窗口)")
    parser.add_argument("--selftest", action="store_true",
                        help="无界面自检后退出(打包流水线用)")
    parser.add_argument("--report", default="",
                        help="自检报告输出路径(配合 --selftest)")
    args = parser.parse_args(argv)

    port = args.port or pick_free_port()
    data = data_root(args.portable)
    token = os.urandom(16).hex()

    if args.selftest:
        report = Path(args.report) if args.report else \
            Path(os.environ.get("TEMP", ".")) / "pdl_selftest.log"
        return selftest(port, data, report)

    from pdl import APP_VERSION
    print(f"{APP_TITLE} v{APP_VERSION}")
    print(f"  数据目录:{data}")
    app = start_server(port, data, token)
    app.config["PDL_TOKEN"] = token
    if not wait_until_ready(port):
        print("服务启动失败,请检查端口占用或杀毒软件拦截。")
        return 1

    if args.web:
        run_browser_mode(port, token, app)
    else:
        rc = run_window_mode(port, app)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
