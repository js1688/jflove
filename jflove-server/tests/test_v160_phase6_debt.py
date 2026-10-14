"""
v1.6.0 Phase 6 · 清债项的服务端判据（AC-B2 / AC-B8）

**AC-B2（BL-2）**：`GET /api/v1/config` 的 OpenAPI 描述必须与实现一致。
实现返回 `{"config": {key: value}}`（`src/services/config_service.py::get_all` 返回 `dict`），
所以描述里必须写「键值对**映射**」；v1.6.0 改前的「列表 / 每项含 `key`、`value`」措辞
必须彻底消失（改前原文见 `文档记录/后端开发记录/v1.6.0.md` §2.1）。

**AC-B8（BL-4）**：`pytest-cov` 只允许出现在 `requirements-dev.txt`（开发/测试专用）；
`requirements.txt` 是**运行时**依赖（Dockerfile 只装它），必须 0 命中测试工具 ——
运行时镜像不得背测试依赖。

判据只面向服务端文件（控制器源码 + 两个 requirements 文件），不碰其它模块。
变异对照（把 `pytest-cov` 写进 `requirements.txt` ⇒ 判据必须失败；把旧措辞写回
⇒ AC-B2 判据必须失败）在 P6-4 的 `poc/editor-probes/r9-negative-matrix.py` 里跑。
"""
from __future__ import annotations

import re
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = SERVER_ROOT / "src" / "controllers" / "config_controller.py"
REQUIREMENTS = SERVER_ROOT / "requirements.txt"
REQUIREMENTS_DEV = SERVER_ROOT / "requirements-dev.txt"

#: 运行时依赖里不该出现的测试工具（`coverage` 覆盖 `pytest-cov` 的依赖面）
TEST_ONLY_TOKENS: tuple[str, ...] = ("pytest-cov", "coverage", "pytest")


def _config_operations(client) -> dict[str, dict]:
    """从**同一份** FastAPI 应用导出 OpenAPI，取 `/config` 路径的操作表"""
    schema = client.app.openapi()
    paths = [item for item in schema.get("paths", {}) if item.endswith("/config")]
    assert len(paths) == 1, f"OpenAPI 里应当恰好有一条 /config 路径，实际：{paths}"
    return schema["paths"][paths[0]]


class TestConfigOpenApiMatchesImplementation:
    """AC-B2：描述与实现对齐，且旧错措辞不再出现"""

    def test_get_config_描述写明是键值对映射(self, client):
        description = _config_operations(client)["get"]["description"]
        assert "键值对映射" in description, description
        assert "{key: value}" in description, description

    def test_get_config_旧错措辞已消失(self, client):
        """负向判据：改前把映射说成了列表，这层措辞不许回归"""
        description = _config_operations(client)["get"]["description"]
        for wrong in ("配置项列表", "每项含", "键值对列表"):
            assert wrong not in description, f"旧措辞回归：{wrong} ⇒ {description}"

    def test_put_config_描述含响应字段与_upsert_语义(self, client):
        description = _config_operations(client)["put"]["description"]
        assert "upsert" in description, description
        assert "`message`" in description, f"PUT 未文档化响应字段：{description}"

    def test_描述字段名与实现一致(self):
        """字形判据之外的一致性判据：描述说的字段名必须真在控制器里被构造出来"""
        source = CONTROLLER.read_text(encoding="utf-8")
        config_builder = re.search(
            r'encrypt_response\(\s*session_id,\s*\{"config":\s*config\}\s*\)', source
        )
        assert config_builder, (
            'GET 的响应字段名与 OpenAPI 描述不再一致（实现里找不到 {"config": config}）'
        )
        assert re.search(r'\{"message":\s*"配置已更新"\}', source), (
            "PUT 的 message 字段与 OpenAPI 描述不再一致"
        )

    def test_两条路由都要求管理员(self):
        """安全宪法 §9.3：带权限语义的接口必须校验角色，改文案时不得顺手删掉"""
        source = CONTROLLER.read_text(encoding="utf-8")
        assert "async def _require_admin(" in source, "管理员校验的单一实现不见了"
        for name in ("get_config", "update_config"):
            assert f"async def {name}(" in source, f"缺少处理器 {name}"
            body = source.split(f"async def {name}(", 1)[1]
            assert "_require_admin(request, db)" in body, f"{name} 不再校验管理员"


class TestRuntimeRequirementsExcludeTestDeps:
    """AC-B8：测试依赖不进运行时依赖"""

    def test_两个依赖文件都存在且非空(self):
        for path in (REQUIREMENTS, REQUIREMENTS_DEV):
            assert path.is_file(), f"缺少 {path.name}"
            assert path.read_text(encoding="utf-8").strip(), f"{path.name} 为空"

    def test_pytest_cov_在开发依赖里(self):
        assert "pytest-cov" in REQUIREMENTS_DEV.read_text(encoding="utf-8")

    def test_运行时依赖零命中测试工具(self):
        lines = [
            line.strip()
            for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        hits = [
            line
            for line in lines
            for token in TEST_ONLY_TOKENS
            if token in line.lower()
        ]
        assert hits == [], f"运行时依赖出现测试工具：{hits}"
