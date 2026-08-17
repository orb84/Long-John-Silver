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
        self._check_embedded_transport(source)
        self._check_no_stdio_runtime_owner()

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


if __name__ == "__main__":
    MCPArchitectureGuard(Path(__file__).resolve().parents[1]).run()
    print("MCP_ARCHITECTURE_PASS")
