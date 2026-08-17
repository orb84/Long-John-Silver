"""Guard the public MCP adapter against orchestration/runtime authority drift."""

from __future__ import annotations

import ast
from pathlib import Path


class MCPArchitectureGuard:
    """Fail when the MCP adapter begins owning private LJS orchestration."""

    _FORBIDDEN_IMPORTS = {
        "src.ai.tool_registry",
        "src.ai.tools",
        "src.main",
        "main",
        "src.core.scheduler",
        "src.core.downloader",
    }
    _FORBIDDEN_PUBLIC_TOOLS = {
        "ljs.search",
        "ljs.download",
        "ljs.execute_action",
        "ljs.tool_execute",
    }

    def __init__(self, root: Path) -> None:
        self._root = root

    def run(self) -> None:
        adapter = self._root / "src/integrations/mcp_server.py"
        source = adapter.read_text()
        tree = ast.parse(source)
        self._check_imports(tree)
        self._check_public_tools(tree)
        self._check_static_resource_signatures(tree)
        self._check_embedded_transport(source)
        self._check_no_stdio_runtime_owner()
        self._check_settings_owned_runtime()

    def _check_imports(self, tree: ast.AST) -> None:
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        violations = sorted(
            name for name in imported
            if any(name == blocked or name.startswith(f"{blocked}.") for blocked in self._FORBIDDEN_IMPORTS)
        )
        assert not violations, f"MCP adapter imports private/runtime authorities: {violations}"

    def _check_public_tools(self, tree: ast.AST) -> None:
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "tool":
                continue
            for keyword in node.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    names.add(str(keyword.value.value))
        forbidden = sorted(names & self._FORBIDDEN_PUBLIC_TOOLS)
        assert not forbidden, f"Forbidden public MCP orchestration tools exposed: {forbidden}"
        assert "ljs.agent_message" in names, "Agent delegation must remain the primary semantic surface"

    @staticmethod
    def _check_static_resource_signatures(tree: ast.AST) -> None:
        """Static MCP resources must not declare SDK-injected Context parameters."""
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Call):
                continue
            registrar = node.func
            if not isinstance(registrar.func, ast.Attribute) or registrar.func.attr != "resource":
                continue
            if not registrar.args or not isinstance(registrar.args[0], ast.Constant):
                continue
            uri = str(registrar.args[0].value)
            if "{" in uri or not node.args or not isinstance(node.args[0], ast.Attribute):
                continue
            method = methods.get(node.args[0].attr)
            assert method is not None, f"Static MCP resource {uri} handler is missing"
            parameters = [arg.arg for arg in method.args.args if arg.arg != "self"]
            parameters += [arg.arg for arg in method.args.kwonlyargs]
            if method.args.vararg is not None:
                parameters.append(f"*{method.args.vararg.arg}")
            if method.args.kwarg is not None:
                parameters.append(f"**{method.args.kwarg.arg}")
            assert not parameters, (
                f"Static MCP resource {uri} cannot declare handler parameters: {parameters}"
            )

    @staticmethod
    def _check_embedded_transport(source: str) -> None:
        assert 'streamable_http_path="/"' in source, "Mounted MCP transport must own only the mount root"
        assert "stdio" not in source.casefold(), "Primary MCP adapter must not bootstrap a stdio domain runtime"

    def _check_no_stdio_runtime_owner(self) -> None:
        integrations = self._root / "src/integrations"
        for path in integrations.glob("*mcp*.py"):
            text = path.read_text().casefold()
            if "stdio" not in text:
                continue
            assert "create_app(" not in text and "scheduler(" not in text, (
                f"{path} appears to combine stdio MCP with an LJS runtime owner"
            )


    def _check_settings_owned_runtime(self) -> None:
        """Keep MCP activation in persisted Settings and one host-owned worker."""
        config_source = (self._root / "src/integrations/mcp_configuration.py").read_text()
        runtime_source = (self._root / "src/integrations/mcp_runtime.py").read_text()
        worker_source = (self._root / "src/integrations/mcp_runtime_worker.py").read_text()
        app_source = (self._root / "src/web/app.py").read_text()
        router_source = (self._root / "src/web/routers/settings.py").read_text()
        panel_source = (self._root / "src/web/static/js/components/settingsPanel.js").read_text()
        src_text = "\n".join(path.read_text(errors="ignore") for path in (self._root / "src").rglob("*.py"))

        assert "from_application" in config_source and "from_environment" not in config_source, (
            "MCP runtime configuration must derive from canonical Settings, not process environment"
        )
        assert "LJS_MCP_ENABLED" not in src_text and "LJS_MCP_TOKEN" not in src_text, (
            "MCP server activation/token must not drift back to environment ownership"
        )
        assert "class MCPRuntimeController" in runtime_source, "Persisted MCP Settings need a runtime controller"
        assert "class MCPRuntimeWorker" in worker_source and "async def _run(" in worker_source, (
            "MCP live lifecycle must remain owned by the dedicated host worker"
        )
        assert 'app.mount("/mcp", mcp_controller.asgi_app' in app_source, (
            "The stable MCP dispatcher must stay mounted in the existing FastAPI process"
        )
        assert '"/api/settings/mcp"' in router_source, "Compass requires the MCP Settings API"
        for control_id in ("pref-mcp-enabled", "pref-mcp-address", "pref-mcp-token", "pref-mcp-status"):
            assert control_id in panel_source, f"Compass MCP panel is missing {control_id}"



if __name__ == "__main__":
    MCPArchitectureGuard(Path(__file__).resolve().parents[1]).run()
    print("MCP_ARCHITECTURE_PASS")
