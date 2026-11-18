"""Generate a read-only inventory of UI network and mutation entry points."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager.cloud_commands import (
    DATABASE_COMMANDS,
    LOCAL_DATABASE_MUTATIONS,
    SERVICE_COMMANDS,
    WEB_SESSION_COMMANDS,
)

UI_ROOT = ROOT / "weimi_manager" / "ui"
OUTPUT = ROOT / "BUTTON_NETWORK_DEPENDENCY_AUDIT.md"
DB_MUTATION_PREFIXES = (
    "add_",
    "apply_",
    "assign_",
    "create_",
    "delete_",
    "import_",
    "rename_",
    "restore_",
    "set_",
    "update_",
    "upsert_",
)
AUTH_METHODS = {
    "login",
    "restore_account",
    "change_password",
    "send_registration_code",
    "complete_registration_with_code",
    "complete_registration_after_link",
    "send_recovery_code",
    "reset_password",
    "redeem",
    "store",
}


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    function: str
    kind: str
    action: str
    command: str
    status: str


class Visitor(ast.NodeVisitor):
    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.functions: list[str] = []
        self.findings: list[Finding] = []

    def _function(self) -> str:
        return ".".join(self.functions) or "<module>"

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    @staticmethod
    def _attribute_chain(node: ast.AST) -> list[str]:
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return list(reversed(parts))

    def visit_Call(self, node: ast.Call) -> None:
        chain = self._attribute_chain(node.func)
        if "service" in chain:
            index = chain.index("service")
            if index + 1 < len(chain):
                method = chain[index + 1]
                command = SERVICE_COMMANDS.get(method, "")
                status = (
                    "适配器已覆盖"
                    if command
                    else (
                        "本地服务访问器"
                        if method == "get"
                        else (
                            "认证链路"
                            if method in AUTH_METHODS
                            else "待分类"
                        )
                    )
                )
                self.findings.append(
                    Finding(
                        self.relative,
                        node.lineno,
                        self._function(),
                        "service",
                        method,
                        command,
                        status,
                    )
                )
        if "web_session" in chain:
            index = chain.index("web_session")
            if index + 1 < len(chain):
                method = chain[index + 1]
                command = WEB_SESSION_COMMANDS.get(method, "")
                status = (
                    "适配器已覆盖"
                    if command
                    else (
                        "本地维护"
                        if method == "cleanup_cache"
                        else "待分类"
                    )
                )
                self.findings.append(
                    Finding(
                        self.relative,
                        node.lineno,
                        self._function(),
                        "web_session",
                        method,
                        command,
                        status,
                    )
                )
        if chain and chain[-1] == "run_task":
            command = ""
            for keyword in node.keywords:
                if keyword.arg == "command" and isinstance(
                    keyword.value,
                    ast.Constant,
                ):
                    command = str(keyword.value.value or "")
            self.findings.append(
                Finding(
                    self.relative,
                    node.lineno,
                    self._function(),
                    "run_task",
                    "run_task",
                    command,
                    "显式授权" if command else "任务入口",
                )
            )
        if (
            chain
            and chain[-1].startswith(DB_MUTATION_PREFIXES)
            and "db" in chain
            and "services" not in chain
        ):
            method = chain[-1]
            command = DATABASE_COMMANDS.get(method, "")
            status = (
                "适配器已覆盖"
                if command
                else (
                    "仅本机状态"
                    if method in LOCAL_DATABASE_MUTATIONS
                    else "本地写入待迁移"
                )
            )
            self.findings.append(
                Finding(
                    self.relative,
                    node.lineno,
                    self._function(),
                    "database",
                    method,
                    command,
                    status,
                )
            )
        self.generic_visit(node)


def main() -> int:
    findings: list[Finding] = []
    for path in sorted(UI_ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        visitor = Visitor(relative)
        visitor.visit(tree)
        findings.extend(visitor.findings)

    findings.sort(key=lambda item: (item.file, item.line, item.action))
    uncovered = [
        item for item in findings
        if item.status in {"待分类", "本地写入待迁移"}
    ]
    lines = [
        "# 按钮-权限-网络依赖审计",
        "",
        "本文件由 `tools/audit_cloud_commands.py` 只读生成。",
        "",
        f"- 扫描入口：{len(findings)}",
        f"- 已明确云端授权：{len(findings) - len(uncovered)}",
        f"- 待分类或本地直写：{len(uncovered)}",
        "",
        "| 文件 | 行 | 方法 | 类型 | 动作 | 命令 | 状态 |",
        "| --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for item in findings:
        lines.append(
            f"| `{item.file}` | {item.line} | `{item.function}` | "
            f"{item.kind} | `{item.action}` | `{item.command}` | "
            f"{item.status} |"
        )
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"WROTE={OUTPUT}")
    print(f"FINDINGS={len(findings)}")
    print(f"UNCOVERED={len(uncovered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
