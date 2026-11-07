"""PersonalDeviceManagement —— 本地个人器件库应用包。"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import security
from .db import db_path, get_db, init_db, log_op
from .paths import AppPaths, as_resources
from .services import boxmap
from .services.serial_bridge import SerialBridge

APP_VERSION = "1.1.1"

# 安全响应头:本地应用也按“最小权限”原则收紧
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

# 仅允许的本机主机名(DNS rebinding 防护)
_LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}

_sync_hook_registered = False


def _host_hostname(host: str) -> str:
    """'127.0.0.1:8321' -> '127.0.0.1';'[::1]:8321' -> '::1';无端口原样。"""
    host = (host or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end > 0 else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def create_app(project_root: str | Path | None = None, *,
               data_root: str | Path | None = None,
               host="127.0.0.1", port=8321,
               shutdown_token: str | None = None) -> Flask:
    """应用工厂。

    project_root:只读资源根(静态前端/固件模板),默认包的上两级(仓库根);
    data_root:   可写数据根(数据库/上传文件),默认与 project_root 相同;
                  桌面模式传入 %LOCALAPPDATA% 目录,使安装目录保持只读。
    shutdown_token:桌面启动器注入的随机令牌;未提供时生命周期端点一律拒绝。
    """
    res = Path(project_root) if project_root else Path(__file__).resolve().parent.parent
    res.mkdir(parents=True, exist_ok=True)
    data = Path(data_root) if data_root else res
    paths = AppPaths(resources=res, data=data)
    data.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, static_folder=str(as_resources(paths) / "pdl" / "static"),
                static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 上传总上限 25MB
    app.config["JSON_AS_ASCII"] = False

    init_db(paths)
    try:
        from .db import daily_backup
        daily_backup(paths)         # T-M1-04:启动自动日备份,保留 7 份
    except Exception:               # 备份失败不阻断启动
        pass

    from .api import _ctx, bp  # 延迟导入避免循环
    import threading
    import time
    bridge = SerialBridge(paths)
    shutdown_event = threading.Event()
    _ctx.update({
        "project_root": paths,
        "bridge": bridge,
        "get_db": lambda: get_db(paths),
        "shutdown_token": shutdown_token,
        "shutdown_event": shutdown_event,
        "last_heartbeat": time.time(),
    })
    app.register_blueprint(bp)

    # 映射变化 → 自动推送 sync 到已连接的器件盒(联动)
    global _sync_hook_registered
    if not _sync_hook_registered:
        def _push_sync(_paths):
            if bridge.connected:
                bridge.push_sync()
        boxmap.register_sync_hook(_push_sync)
        _sync_hook_registered = True

    @app.get("/")
    def index():
        return send_from_directory(str(as_resources(paths) / "pdl" / "static"),
                                   "index.html")

    @app.before_request
    def local_api_guard():
        """API 守卫:防 DNS rebinding + 防 跨站请求伪造(drive-by CSRF)。

        - Host 头必须是本机回环主机名(阻断 rebinding 到攻击者域名);
        - 写请求(非 GET/HEAD/OPTIONS)必须同源:
          Sec-Fetch-Site 为 cross-site 时拒绝;Origin 存在且主机不匹配时拒绝。
        浏览器现代版本都会自动带 Sec-Fetch-Site;不带这些头的本地工具
        (curl/脚本)不受影响,守卫只针对浏览器发起的跨站写请求。
        """
        if not request.path.startswith("/api"):
            return None
        if _host_hostname(request.host) not in _LOCAL_HOSTNAMES:
            return jsonify({"ok": False,
                            "error": "仅允许从本机访问 API"}), 403
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        site = (request.headers.get("Sec-Fetch-Site") or "").lower()
        if site and site not in ("same-origin", "same-site", "none"):
            return jsonify({"ok": False, "error": "已拒绝跨站写请求"}), 403
        origin = request.headers.get("Origin")
        if origin:
            try:
                from urllib.parse import urlsplit
                o_host = _host_hostname(urlsplit(origin).netloc)
            except ValueError:
                o_host = ""
            if o_host not in _LOCAL_HOSTNAMES:
                return jsonify({"ok": False, "error": "已拒绝跨源写请求"}), 403
        return None

    @app.errorhandler(security.ValidationError)
    def on_validation_error(exc):
        try:
            if request.method in ("POST", "PUT", "DELETE") and request.path.startswith("/api"):
                log_op(paths, "fail", request.path, str(exc), ok=False)
        except Exception:  # 日志失败不影响错误响应
            pass
        return jsonify({"ok": False, "error": str(exc)}), 400

    @app.errorhandler(404)
    def on_404(_):
        if request.path.startswith("/api"):
            return jsonify({"ok": False, "error": "接口不存在"}), 404
        return jsonify({"ok": False, "error": "页面不存在"}), 404

    @app.errorhandler(413)
    def on_too_large(_):
        return jsonify({"ok": False, "error": "上传内容超过 25MB 上限"}), 413

    @app.errorhandler(Exception)
    def on_error(exc):
        import traceback
        app.logger.error("未处理异常:%s\n%s", exc, traceback.format_exc())
        try:
            log_op(paths, "fail", request.path,
                   f"{exc.__class__.__name__}: {exc}", ok=False)
        except Exception:  # 日志失败不影响错误响应
            pass
        return jsonify({"ok": False,
                        "error": f"服务器内部错误:{exc.__class__.__name__}"}), 500

    @app.after_request
    def add_security_headers(resp):
        for k, v in _SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp

    app.config["PDL_HOST"] = host
    app.config["PDL_PORT"] = port
    app.config["PDL_PATHS"] = paths
    app.config["PDL_SHUTDOWN_EVENT"] = shutdown_event
    app.config["PDL_DATA_ROOT"] = data
    return app


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="PersonalDeviceManagement 本地个人器件库")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址(默认仅本机)")
    parser.add_argument("--port", type=int, default=8321, help="端口(默认 8321)")
    parser.add_argument("--data-root", default=None,
                        help="数据目录(默认 <项目>/data;桌面模式由启动器指定)")
    args = parser.parse_args()

    app = create_app(port=args.port, data_root=args.data_root)
    print(f"\n  个人器件库 PersonalDeviceManagement v{APP_VERSION}")
    print(f"  http://{args.host}:{args.port}  (Ctrl+C 退出)\n")
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
