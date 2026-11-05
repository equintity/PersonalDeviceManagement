"""PersonalDeviceManagement —— 本地个人器件库应用包。"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, send_from_directory

from . import security
from .db import get_db, init_db
from .services import boxmap
from .services.serial_bridge import SerialBridge

APP_VERSION = "1.0.0"

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

_sync_hook_registered = False


def create_app(project_root: str | Path | None = None, *, host="127.0.0.1", port=8321) -> Flask:
    """应用工厂。

    project_root:数据(数据库/上传文件)与固件模板所在目录,
                  默认取包的上两级(即仓库根目录)。
    """
    root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent
    root.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, static_folder=str(root / "pdl" / "static"),
                static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 上传总上限 25MB
    app.config["JSON_AS_ASCII"] = False

    init_db(root)

    from .api import _ctx, bp  # 延迟导入避免循环
    bridge = SerialBridge(root)
    _ctx["project_root"] = root
    _ctx["bridge"] = bridge
    _ctx["get_db"] = lambda: get_db(root)
    app.register_blueprint(bp)

    # 映射变化 → 自动推送 sync 到已连接的器件盒(联动)
    global _sync_hook_registered
    if not _sync_hook_registered:
        def _push_sync(r):
            if bridge.connected:
                bridge.push_sync()
        boxmap.register_sync_hook(_push_sync)
        _sync_hook_registered = True

    @app.get("/")
    def index():
        return send_from_directory(str(root / "pdl" / "static"), "index.html")

    @app.errorhandler(security.ValidationError)
    def on_validation_error(exc):
        from flask import jsonify
        return jsonify({"ok": False, "error": str(exc)}), 400

    @app.errorhandler(404)
    def on_404(_):
        from flask import jsonify
        if request_path_is_api():
            return jsonify({"ok": False, "error": "接口不存在"}), 404
        return jsonify({"ok": False, "error": "页面不存在"}), 404

    def request_path_is_api() -> bool:
        from flask import request
        return request.path.startswith("/api")

    @app.errorhandler(413)
    def on_too_large(_):
        from flask import jsonify
        return jsonify({"ok": False, "error": "上传内容超过 25MB 上限"}), 413

    @app.errorhandler(Exception)
    def on_error(exc):
        import traceback
        app.logger.error("未处理异常:%s\n%s", exc, traceback.format_exc())
        from flask import jsonify
        return jsonify({"ok": False, "error": f"服务器内部错误:{exc.__class__.__name__}"}), 500

    @app.after_request
    def add_security_headers(resp):
        for k, v in _SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp

    app.config["PDL_HOST"] = host
    app.config["PDL_PORT"] = port
    return app


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="PersonalDeviceManagement 本地个人器件库")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址(默认仅本机)")
    parser.add_argument("--port", type=int, default=8321, help="端口(默认 8321)")
    args = parser.parse_args()

    app = create_app(port=args.port)
    print(f"\n  个人器件库 PersonalDeviceManagement v{APP_VERSION}")
    print(f"  http://{args.host}:{args.port}  (Ctrl+C 退出)\n")
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
