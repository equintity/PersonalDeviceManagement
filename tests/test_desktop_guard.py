"""桌面版安全守卫测试:防 DNS rebinding / 跨站写请求 / 生命周期端点令牌。"""

from pdl import create_app
from pdl.api import _ctx


class TestLocalGuard:
    def test_non_local_host_rejected(self, client):
        """伪造 Host(DNS rebinding 攻击面)必须被拒。"""
        resp = client.get("/api/system/info", headers={"Host": "evil.example.com"})
        assert resp.status_code == 403

    def test_local_hosts_allowed(self, client):
        for host in ("127.0.0.1:8321", "localhost", "localhost:8321", "[::1]:8321"):
            resp = client.get("/api/system/info", headers={"Host": host})
            assert resp.status_code == 200, host

    def test_cross_site_write_rejected(self, client):
        resp = client.post("/api/box/clear",
                           headers={"Sec-Fetch-Site": "cross-site"})
        assert resp.status_code == 403
        resp = client.post("/api/box/clear",
                           headers={"Origin": "https://evil.example.com"})
        assert resp.status_code == 403

    def test_same_origin_write_allowed(self, client):
        resp = client.post("/api/box/clear",
                           headers={"Sec-Fetch-Site": "same-origin"})
        assert resp.status_code == 200

    def test_non_browser_write_allowed(self, client):
        """本地工具(curl/脚本)没有 Sec-Fetch-Site/Origin 头,不受影响。"""
        assert client.post("/api/box/clear").status_code == 200

    def test_cross_site_read_still_allowed(self, client):
        """只读请求不拦(无敏感数据,页面资源也要能加载)。"""
        resp = client.get("/api/system/info",
                          headers={"Sec-Fetch-Site": "cross-site"})
        assert resp.status_code == 200


class TestLifecycleEndpoints:
    def test_shutdown_disabled_without_token(self, client):
        """源码/服务器模式未注入令牌:端点一律拒绝,网页无法关闭服务。"""
        assert client.post("/api/system/shutdown").status_code == 403
        assert client.post("/api/system/heartbeat").status_code == 403

    def test_shutdown_requires_exact_token(self, app_root):
        app = create_app(app_root, shutdown_token="s3cret")
        c = app.test_client()
        assert c.post("/api/system/shutdown",
                      headers={"X-PDL-Token": "bad"}).status_code == 403
        resp = c.post("/api/system/shutdown",
                      headers={"X-PDL-Token": "s3cret"})
        assert resp.status_code == 200
        assert _ctx["shutdown_event"].is_set()

    def test_heartbeat_updates_timestamp(self, app_root):
        import time
        app = create_app(app_root, shutdown_token="t")
        c = app.test_client()
        before = _ctx["last_heartbeat"]
        time.sleep(0.01)
        assert c.post("/api/system/heartbeat",
                      headers={"X-PDL-Token": "t"}).status_code == 200
        assert _ctx["last_heartbeat"] > before
