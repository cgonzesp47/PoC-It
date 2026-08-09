from __future__ import annotations

from typing import List

from poc_it.generador.json_utils import extraer_json_tolerante

from .models import CodegenIssue, GeneratedFile


def parse_single_file_response(
    *,
    raw: str,
    expected_path: str,
    allow_empty_content: bool = False,
) -> tuple[GeneratedFile | None, List[CodegenIssue]]:
    expected_path = (expected_path or "").replace("\\", "/")
    data = extraer_json_tolerante(raw) or {}
    if not isinstance(data, dict) or not data:
        return None, [
            CodegenIssue(
                code="CODEGEN_JSON_PARSE_FAILED",
                path=expected_path,
                message="No se pudo parsear JSON válido desde la respuesta del LLM.",
            )
        ]

    files = data.get("files")
    if isinstance(files, list) and len(files) > 1:
        return None, [
            CodegenIssue(
                code="CODEGEN_MULTIPLE_FILES_RETURNED",
                path=expected_path,
                message="La respuesta devolvió múltiples archivos para un contrato de archivo único.",
            )
        ]

    candidate = data
    if isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict):
        candidate = files[0]

    if not isinstance(candidate, dict):
        return None, [
            CodegenIssue(
                code="CODEGEN_SINGLE_FILE_EXTRACT_FAILED",
                path=expected_path,
                message="No se pudo extraer un archivo único desde la respuesta.",
            )
        ]

    path_value = candidate.get("path")
    content_value = candidate.get("content")

    if not isinstance(path_value, str) or not isinstance(content_value, str):
        return None, [
            CodegenIssue(
                code="CODEGEN_SINGLE_FILE_EXTRACT_FAILED",
                path=expected_path,
                message="La respuesta no contiene los campos path/content esperados.",
            )
        ]

    normalized_path = path_value.replace("\\", "/")
    if normalized_path != expected_path:
        return None, [
            CodegenIssue(
                code="CODEGEN_PATH_MISMATCH",
                path=expected_path,
                message=f"El archivo devuelto no coincide con el path esperado: {normalized_path}",
            )
        ]

    if not content_value.strip() and not allow_empty_content:
        return None, [
            CodegenIssue(
                code="CODEGEN_EMPTY_CONTENT",
                path=expected_path,
                message="El contenido generado está vacío y el contrato no lo permite.",
            )
        ]

    return GeneratedFile(path=normalized_path, content=content_value), []
