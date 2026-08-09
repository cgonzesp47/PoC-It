from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class IncompleteCodeFinding:
    kind: str
    line: int | None
    evidence: str


_TEXT_PLACEHOLDER_PATTERNS = (
    "... existing code ...",
    "# existing code",
    "# todo: implement",
)


def _function_is_stub(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    body = list(node.body)

    if not body:
        return True

    if (
        len(body) == 1
        and isinstance(
            body[0],
            ast.Pass,
        )
    ):
        return True

    if (
        len(body) == 1
        and isinstance(
            body[0],
            ast.Expr,
        )
        and isinstance(
            body[0].value,
            ast.Constant,
        )
        and isinstance(
            body[0].value.value,
            str,
        )
    ):
        return True

    if (
        len(body) == 1
        and isinstance(
            body[0],
            ast.Raise,
        )
        and isinstance(
            body[0].exc,
            ast.Call,
        )
        and isinstance(
            body[0].exc.func,
            ast.Name,
        )
        and (
            body[0]
            .exc
            .func
            .id
            == "NotImplementedError"
        )
    ):
        return True

    return False


def detect_incomplete_code(
    path: str,
    content: str,
) -> list[IncompleteCodeFinding]:
    findings: list[IncompleteCodeFinding] = []

    lowered = content.casefold()

    for pattern in _TEXT_PLACEHOLDER_PATTERNS:
        if pattern.casefold() in lowered:
            findings.append(
                IncompleteCodeFinding(
                    kind="text_placeholder",
                    line=None,
                    evidence=pattern,
                )
            )

    if not path.endswith(".py"):
        return findings

    try:
        tree = ast.parse(
            content,
            filename=path,
        )
    except SyntaxError:
        return findings

    for node in ast.walk(tree):
        if not isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        ):
            continue

        if _function_is_stub(node):
            findings.append(
                IncompleteCodeFinding(
                    kind="function_stub",
                    line=getattr(
                        node,
                        "lineno",
                        None,
                    ),
                    evidence=node.name,
                )
            )

    return findings
