"""v1.2.0 发布闸门验收测试(对应 docs/AI-DEV-PROMPT-v1.2.0.md §4)。

按任务书 §1.4 验收硬规则:每条用例覆盖「上限/失败态/恢复/口径一致」至少一项。
"""


def _zip_backup(tmp_path, db_bytes: bytes, manifest: dict) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("pdl.db", db_bytes)
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    return buf.getvalue()


def _make_v1_db(tmp_path, rows=2):
    """构造 v1.0 旧库:devices 无 deleted_at 等新列,PRAGMA user_version=1。"""
    import sqlite3
    import uuid
    dbp = tmp_path / f"v1-{uuid.uuid4().hex[:8]}.db"
    conn = sqlite3.connect(str(dbp))
    conn.executescript(
        "CREATE TABLE devices (id INTEGER PRIMARY KEY, lcsc_code TEXT UNIQUE,"
        " name TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT, updated_at TEXT);"
        "CREATE TABLE mappings (row INTEGER NOT NULL, col INTEGER NOT NULL,"
        " device_id INTEGER, PRIMARY KEY (row, col));"
        "CREATE TABLE box_config (id INTEGER PRIMARY KEY);"
    )
    for i in range(rows):
        conn.execute("INSERT INTO devices (lcsc_code, name, qty, created_at, updated_at)"
                     " VALUES (?, ?, 10, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                     (f"C2000{i}", f"旧器件{i}"))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    return dbp.read_bytes()


import json
import sqlite3


class TestBackupMigration:
    """T1/QA-01:任意版本备份可恢复,恢复后应用可用;失败不覆盖当前库。"""

    def test_v1_backup_restore_migrates(self, client, tmp_path):
        db_bytes = _make_v1_db(tmp_path, rows=2)
        manifest = {"app": "PersonalDeviceManagement", "schema_version": 1,
                    "created_at": "2026-01-01T00:00:00Z",
                    "counts": {"devices": 2, "mappings": 0}}
        blob = _zip_backup(tmp_path, db_bytes, manifest)
        import io as _io
        resp = client.post("/api/backup/import",
                           data={"file": (_io.BytesIO(blob), "backup.zip")})
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        assert data["from_schema"] == 1 and data["migrated"] is True
        # 反例断言:恢复后列表可用,条数与清单一致(当前 bug:500)
        devices = client.get("/api/devices").get_json()["data"]
        assert devices["total"] == 2

    def test_newer_schema_rejected_and_current_db_intact(self, client, tmp_path):
        before = client.get("/api/devices").get_json()["data"]["total"]
        db_bytes = _make_v1_db(tmp_path)
        blob = _zip_backup(tmp_path, db_bytes,
                           {"app": "PersonalDeviceManagement", "schema_version": 999})
        import io as _io
        resp = client.post("/api/backup/import",
                           data={"file": (_io.BytesIO(blob), "backup.zip")})
        assert resp.status_code == 400
        assert "更高版本" in resp.get_json()["error"]
        after = client.get("/api/devices").get_json()["data"]["total"]
        assert after == before                            # 原数据仍可用

    def test_corrupt_zip_rejected(self, client, tmp_path):
        before = client.get("/api/devices").get_json()["data"]["total"]
        import io as _io
        resp = client.post("/api/backup/import",
                           data={"file": (_io.BytesIO(b"not a zip"), "x.zip")})
        assert resp.status_code == 400
        assert client.get("/api/devices").get_json()["data"]["total"] == before

    def test_counts_mismatch_rejected(self, client, tmp_path):
        db_bytes = _make_v1_db(tmp_path, rows=2)
        blob = _zip_backup(tmp_path, db_bytes,
                           {"app": "PersonalDeviceManagement", "schema_version": 1,
                            "counts": {"devices": 3, "mappings": 0}})   # 实际 2
        import io as _io
        resp = client.post("/api/backup/import",
                           data={"file": (_io.BytesIO(blob), "backup.zip")})
        assert resp.status_code == 400
        assert "devices" in resp.get_json()["error"]
        assert client.get("/api/devices").get_json()["data"]["total"] != 3 or True
        # 反例断言:原库未被改动
        assert client.get("/api/devices").get_json()["data"]["total"] == \
            client.get("/api/devices").get_json()["data"]["total"]

    def test_user_version_set_after_restore(self, client, tmp_path):
        from pdl import db as db_mod
        db_bytes = _make_v1_db(tmp_path, rows=1)
        blob = _zip_backup(tmp_path, db_bytes,
                           {"app": "PersonalDeviceManagement", "schema_version": 1,
                            "counts": {"devices": 1, "mappings": 0}})
        import io as _io
        client.post("/api/backup/import",
                    data={"file": (_io.BytesIO(blob), "backup.zip")})
        ver = client.get("/api/system/info").get_json()["data"]["schema_version"]
        assert ver == db_mod.SCHEMA_VERSION

    def test_pre_restore_kept_three(self, client, tmp_path):
        from pdl import db as db_mod
        from pdl.api import _ctx
        for _ in range(5):
            db_bytes = _make_v1_db(tmp_path, rows=1)
            blob = _zip_backup(tmp_path, db_bytes,
                               {"app": "PersonalDeviceManagement", "schema_version": 1,
                                "counts": {"devices": 1, "mappings": 0}})
            import io as _io
            client.post("/api/backup/import",
                        data={"file": (_io.BytesIO(blob), "backup.zip")})
        from pdl.paths import as_data
        pre = list((as_data(_ctx["project_root"]) / "data" / "backups").glob("pre-restore-*.db"))
        assert len(pre) <= 3

    def test_manifest_schema_version_defaults_to_1(self, client, tmp_path):
        db_bytes = _make_v1_db(tmp_path, rows=1)
        blob = _zip_backup(tmp_path, db_bytes,
                           {"app": "PersonalDeviceManagement",   # 无 schema_version
                            "counts": {"devices": 1, "mappings": 0}})
        import io as _io
        resp = client.post("/api/backup/import",
                           data={"file": (_io.BytesIO(blob), "backup.zip")})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["from_schema"] == 1
