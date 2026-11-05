"""pytest 共享夹具:临时项目根 + 测试客户端。"""

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pdl import create_app  # noqa: E402
from pdl.db import init_db  # noqa: E402


@pytest.fixture()
def app_root(tmp_path):
    """临时项目根:附带固件模板(代码生成需要),并完成建表。"""
    shutil.copytree(REPO_ROOT / "firmware" / "template",
                    tmp_path / "firmware" / "template")
    init_db(tmp_path)
    return tmp_path


@pytest.fixture()
def client(app_root):
    app = create_app(app_root)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture()
def root(app_root):
    return app_root
