from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from poc_it.generador.file_contracts import _INTEGRATION_CLIENT_PROVIDER_SYMBOL


def _safe_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _spec_summary(spec: dict) -> dict:
    if not isinstance(spec, dict):
        return {}
    return {
        "schema_version": spec.get("schema_version"),
        "entrypoint": spec.get("entrypoint"),
        "run_command": spec.get("run_command"),
        "imports_policy": spec.get("imports_policy"),
        "dependencies": spec.get("dependencies", []),
        "dev_dependencies": spec.get("dev_dependencies", []),
        "technology_signals": spec.get("technology_signals", []),
        "integrations": spec.get("integrations", []),
        "configuration": spec.get("configuration", []),
        "implementation_files": spec.get("implementation_files", []),
        "endpoints": [
            {
                "method": e.get("method"),
                "path": e.get("path"),
                "file": e.get("file"),
                "func": e.get("func"),
                "actions": e.get("actions", []),
                "errors": e.get("errors", []),
                "integration_refs": e.get("integration_refs", []),
            }
            for e in (spec.get("endpoints") or [])
            if isinstance(e, dict)
        ],
        "restrictions": spec.get("restrictions", []),
        "files": spec.get("files", []),
    }


def _kind_specific_rules(file_contract: Dict[str, Any]) -> List[str]:
    kind = str(file_contract.get("kind") or "").strip()
    if kind == "endpoint":
        return [
            "No construyas directamente clientes externos si existe un File Contract de integración relacionado.",
            "Invoca el módulo interno asignado.",
            "Traduce errores declarados a respuestas HTTP.",
            "No dupliques autenticación o configuración del proveedor.",
            "Si el módulo interno que invocas construye un cliente externo (Google/AWS/HTTP/etc.), "
            "importa también su función proveedora del cliente — llamada EXACTAMENTE "
            f"`{_INTEGRATION_CLIENT_PROVIDER_SYMBOL}` (no inventes otro nombre) — y recíbela vía "
            f"`Depends({_INTEGRATION_CLIENT_PROVIDER_SYMBOL})` como parámetro del handler; pasa "
            "ese cliente como argumento en la llamada directa a la función de la acción (la "
            "llamada sigue siendo directa, solo cambia de dónde sale el cliente).",
        ]
    if kind == "integration":
        return [
            "Construye el cliente externamente de forma lazy.",
            "Lee configuración mediante app.core.config.",
            "Implementa una operación llamable por cada acción externa asignada.",
            "No importes FastAPI salvo que el contrato lo permita expresamente.",
            "No traduzcas excepciones a HTTPException.",
            "Separa \"construir el cliente\" de \"usar el cliente\": expón una función proveedora "
            f"sin argumentos requeridos llamada EXACTAMENTE `{_INTEGRATION_CLIENT_PROVIDER_SYMBOL}` "
            "(no un nombre distinto — el endpoint que la consume espera literalmente ese nombre) "
            "que construye y devuelve el cliente, y haz que cada función de acción reciba ese "
            "cliente como parámetro explícito en vez de construirlo/importarlo por dentro.",
        ]
    if kind == "config":
        return [
            "Declara toda la configuración asignada.",
            "No exige valores en import-time.",
            "No almacenes valores reales.",
        ]
    if kind == "requirements":
        return [
            "Cada línea debe ser un package instalable declarado en dependencies.",
            "No escribas nombres de módulos de importación salvo que coincidan con el package declarado.",
        ]
    return []


def _render_bullets(values: List[Any]) -> str:
    items = []
    for value in values or []:
        items.append(f"- {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(items) if items else "- []"


def generic_python_correctness_rules() -> List[str]:
    """
    Reglas de corrección de Python genéricas (no atadas a ningún SDK, tecnología ni plantilla
    concreta), derivadas de bugs reales y repetidos observados en generación de código.

    Compartidas entre el prompt de codegen/reparación de contrato (este módulo) y el prompt de
    reparación runtime (`poc_it.orquestacion.llm_safe_repair`), para que ambos caminos se
    beneficien de las mismas lecciones aprendidas sin duplicar texto ni desincronizarse.
    """
    return [
        "- Para ficheros temporales usa el módulo `tempfile` (p.ej. `tempfile.gettempdir()`, "
        "`tempfile.NamedTemporaryFile`); no hardcodees rutas de directorio como `/tmp/...` "
        "o `C:\\Temp\\...`, que no son portables entre sistemas operativos.",
        "- Si usas acceso con puntos a un submódulo (p.ej. `paquete.submodulo.funcion()`), debes "
        "importar ese submódulo explícitamente (`import paquete.submodulo`); un "
        "`from paquete.submodulo import X` NO deja `paquete` disponible como nombre en el scope, "
        "así que escribir `paquete.submodulo.funcion()` tras ese import fallaría con "
        "'paquete' is not defined.",
        "- Cuando una función auxiliar devuelva un dict con varios campos, en el punto de llamada "
        "usa exactamente el campo que necesitas (p.ej. `resultado['campo']`), nunca el dict "
        "completo como si fuera un único valor escalar. Antes de devolver una respuesta, "
        "comprueba que cada valor coincide con el tipo declarado en la firma/response model "
        "(si una función declara `-> Dict[str, str]`, ningún valor del dict puede ser a su vez "
        "un dict u otro objeto no-string).",
        "- Cualquier valor que extraigas de la respuesta de un SDK/cliente externo (Google/AWS/HTTP/"
        "base de datos/etc., p.ej. `response['id']`, `resource.get('name')`) tiene un tipo dinámico "
        "que NO puedes garantizar en tiempo de escritura del código: conviértelo explícitamente al "
        "tipo declarado en tu `response_model` antes de construirlo (`str(...)`, `int(...)`, "
        "`bool(...)`). NUNCA pases ese valor extraído directamente a un campo tipado sin conversión "
        "— si el SDK real (o un doble/mock usado en tests) devuelve algo que no es exactamente ese "
        "tipo primitivo, la validación de Pydantic del `response_model` fallará con un 500 en vez de "
        "devolver la respuesta.",
        "- El código de limpieza en un bloque `finally` (cerrar/borrar ficheros temporales, liberar "
        "recursos, etc.) nunca debe poder lanzar una excepción que reemplace la respuesta o el error "
        "que ya se estaba devolviendo (p.ej. borrar un fichero temporal que otra librería aún tiene "
        "abierto). Envuelve esa limpieza en su propio `try/except Exception as exc:` para que un fallo "
        "de limpieza nunca oculte el resultado real del endpoint — y dentro de ese except registra el "
        "fallo con `logger.exception(...)` (nunca lo silencies con un `pass` vacío: cualquier "
        "`except Exception` sin logging trazable se rechaza igualmente).",
    ]


def _priority_rules() -> List[str]:
    return [
        "ORDEN DE PRIORIDAD",
        "1. must_not",
        "2. must_implement",
        "3. implementation_plan",
        "4. provided_interfaces y required_internal_calls",
        "5. actions, errors e integrations",
        "6. responsibilities",
        "7. SPEC resumido",
        "8. contexto normalizado",
        "",
        "- Debes implementar TODAS las entradas de must_implement.",
        "- Está prohibido vulnerar CUALQUIER entrada de must_not.",
        "- Una acción required=true no puede omitirse ni sustituirse por TODO, pass, placeholder o respuesta simulada.",
        '- implementation_level="integration_skeleton" exige cliente, autenticación, configuración y operación llamable; no exige credenciales reales.',
        "- No devuelvas una respuesta de éxito si no se ejecutaron las acciones requeridas.",
        "- Las llamadas externas deben ser lazy y mockeables.",
        "- Los tests herméticos no deben llamar a servicios reales.",
        "- Cada provided_interface debe existir como símbolo Python real.",
        "- No cambies el nombre de ningún símbolo declarado.",
        "- Cada required_internal_call required=true debe importarse e invocarse.",
        "- No dupliques en el consumidor la implementación del proveedor.",
        "- No sustituyas una llamada requerida por una respuesta constante.",
        "- No añadas nuevos campos de configuración, nuevos secrets ni nuevas restricciones contractuales.",
        "- Si falta información en el contrato, no la inventes.",
        *generic_python_correctness_rules(),
    ]


def _summarize_related_contract(contract: Dict[str, Any]) -> Dict[str, Any]:
    configuration_access = contract.get("configuration_access") or {}
    return {
        "path": contract.get("path"),
        "kind": contract.get("kind"),
        "required_symbols": contract.get("required_symbols") or [],
        "provided_interfaces": contract.get("provided_interfaces") or [],
        "required_internal_calls": contract.get("required_internal_calls") or [],
        "integration_refs": contract.get("integration_refs") or [],
        "configuration": configuration_access,
        "configuration_access": configuration_access,
        "authentication_constraints": contract.get("authentication_constraints") or {},
        "authentication_runtime_contract": (
            contract.get("authentication_runtime_contract") or {}
        ),
        "data_contracts": contract.get("data_contracts") or {},
        "dependencies": contract.get("dependencies") or [],
    }


def _summarize_related_contracts(contracts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_summarize_related_contract(contract) for contract in contracts]


def _configuration_contract_section(file_contract: Dict[str, Any]) -> List[str]:
    configuration_access = file_contract.get("configuration_access") or {}
    provider_module = str(configuration_access.get("provider_module") or "").strip()
    provider_symbol = str(configuration_access.get("provider_symbol") or "").strip()
    allowed_fields = [
        str(field).strip()
        for field in (configuration_access.get("allowed_fields") or [])
        if str(field).strip()
    ]
    access_mode = str(configuration_access.get("access_mode") or "").strip()
    if not provider_module or not provider_symbol:
        return []

    return [
        "CONFIGURATION CONTRACT",
        "",
        "Provider:",
        f"{provider_module}:{provider_symbol}",
        "",
        "Access mode:",
        access_mode or "unspecified",
        "",
        "Allowed fields:",
        *([f"- {field}" for field in allowed_fields] or ["- []"]),
        "",
        "STRICT RULES:",
        "- Do not reference configuration fields not listed above.",
        "- Do not invent environment variables.",
        "- Do not add new settings fields.",
        "- If required information is not represented in this contract, do not guess it.",
    ]


def _authentication_contract_section(file_contract: Dict[str, Any]) -> List[str]:
    authentication_constraints = file_contract.get("authentication_constraints") or {}
    authentication_runtime_contract = (
        file_contract.get("authentication_runtime_contract") or {}
    )
    if not authentication_constraints and not authentication_runtime_contract:
        return []

    sections: List[str] = []
    if authentication_constraints:
        credential_source = (
            str(authentication_constraints.get("credential_source") or "").strip()
            or "unknown"
        )
        allows_embedded_secret = bool(
            authentication_constraints.get("allows_embedded_secret")
        )
        allows_static_credential_file = bool(
            authentication_constraints.get("allows_static_credential_file", True)
        )
        sections.extend(
            [
                "AUTHENTICATION CONTRACT — MUST NOT BE VIOLATED",
                "",
                f"credential_source:\n{credential_source}",
                "",
                f"allows_embedded_secret:\n{str(allows_embedded_secret).lower()}",
                "",
                f"allows_static_credential_file:\n{str(allows_static_credential_file).lower()}",
                "",
                "You MUST choose an implementation compatible with these constraints.",
                "Do not invent additional credential configuration.",
                "Do not change the credential source.",
                "Do not introduce a static credential file if forbidden.",
                "If the technology supports multiple authentication APIs, choose one compatible with the declared contract.",
                "",
            ]
        )

    if authentication_runtime_contract:
        discovery = (
            str(authentication_runtime_contract.get("discovery") or "").strip()
            or "unspecified"
        )
        requires_configuration_field = authentication_runtime_contract.get(
            "requires_configuration_field"
        )
        requires_static_credential_file = authentication_runtime_contract.get(
            "requires_static_credential_file"
        )
        requires_embedded_secret = authentication_runtime_contract.get(
            "requires_embedded_secret"
        )
        sections.extend(
            [
                "AUTHENTICATION RUNTIME CONTRACT",
                "",
                "Discovery:",
                discovery,
                "",
                "Requires configuration field:",
                str(requires_configuration_field).lower(),
                "",
                "Requires static credential file:",
                str(requires_static_credential_file).lower(),
                "",
                "Requires embedded secret:",
                str(requires_embedded_secret).lower(),
                "",
                "STRICT RULES:",
                "- Obtain credentials from the runtime environment or ambient credential mechanism supported by the selected library.",
                "- Do not introduce a new configuration field for credentials when `requires_configuration_field=false`.",
                "- Do not introduce a credential file when `requires_static_credential_file=false`.",
                "- Do not embed secret values when `requires_embedded_secret=false`.",
                "- Use only configuration fields listed in CONFIGURATION CONTRACT.",
                "- If the selected library offers multiple authentication methods, choose one compatible with this contract.",
                "- NEVER pass `None`, an empty string, or any other placeholder/sentinel value as a "
                "required argument when constructing a credentials/client object — an argument "
                "you cannot satisfy from CONFIGURATION CONTRACT or from ambient discovery means "
                "you picked the wrong constructor/method for this contract, not that the argument "
                "can be omitted.",
                "- When `discovery=ambient` and `requires_static_credential_file=false`: use the "
                "selected library's zero-argument (or scopes-only) ambient-credential entry point "
                "(the one that auto-discovers credentials from the runtime environment). Do NOT "
                "call a 'from file' / 'from path' / 'from json' style constructor at all, not even "
                "with a placeholder path.",
            ]
        )

    return sections


def build_prompt_file_contract(
    *,
    spec: dict,
    file_contract: dict,
    related_contracts: List[dict],
    descripcion_global: str,
    contexto_normalizado: Optional[dict] = None,
) -> str:
    contexto_normalizado = (
        contexto_normalizado if isinstance(contexto_normalizado, dict) else {}
    )
    fc_path = (file_contract.get("path") or "").replace("\\", "/")
    related_summary = _summarize_related_contracts(related_contracts)

    prompt_lines = [
        "TAREA",
        "Genera EXACTAMENTE UN (1) archivo para un proyecto FastAPI, cumpliendo estrictamente el FileContract.",
        "",
        "REGLAS DE SALIDA (OBLIGATORIAS)",
        "- Devuelve SOLO JSON válido, sin texto extra.",
        "- PROHIBIDO usar ``` o markdown.",
        "- Formato preferido (devuelve EXACTAMENTE estas claves):",
        "{",
        f'  "path": "{fc_path}",',
        '  "content": "..."',
        "}",
        '- NO devuelvas "files" con varios archivos.',
        "- NO generes otros archivos.",
        '- El campo "content" debe ser el archivo COMPLETO (no fragmentos).',
        "",
        "REGLAS CONTRACT-FIRST (NO NEGOCIABLE)",
        "- FileContract es la fuente de verdad para ESTE archivo.",
        "- No crear símbolos públicos fuera de required_symbols.",
        "- No mover símbolos a otros archivos.",
        "- No expandas SPEC, ImplementationContract ni FileContract para acomodar el código generado.",
        "",
        "INVARIANTES DE CÓDIGO (si es .py)",
        "- Debe ser Python parseable (sin SyntaxError).",
        "- Imports internos deben ser absolutos desde `app.*`.",
        "- Prohibido ejecutar conexiones externas en import-time (DB, APIs, etc.).",
        "- No validar variables de entorno obligatorias en import-time.",
        "",
        *_priority_rules(),
        "",
        "REGLAS ESPECÍFICAS DEL KIND",
        *_kind_specific_rules(file_contract),
        "",
        *_configuration_contract_section(file_contract),
        "",
        *_authentication_contract_section(file_contract),
        "",
        "DESCRIPCIÓN GLOBAL",
        descripcion_global,
        "",
        "FILE CONTRACT (ACTUAL, FUENTE DE VERDAD)",
        _safe_json(file_contract),
        "",
        "CONTRATOS RELACIONADOS (RESUMEN INTER-ARCHIVO; SOLO CONTEXTO)",
        _safe_json(related_summary),
        "",
        "SPEC (RESUMEN; REFERENCIA SECUNDARIA)",
        _safe_json(_spec_summary(spec)),
        "",
        "CONTEXTO NORMALIZADO (RESUMEN; REFERENCIA)",
        _safe_json(
            {
                "objetivo_tecnico": contexto_normalizado.get("objetivo_tecnico"),
                "restricciones_tecnicas": contexto_normalizado.get(
                    "restricciones_tecnicas", []
                ),
                "integraciones_externas": contexto_normalizado.get(
                    "integraciones_externas", []
                ),
                "persistence": contexto_normalizado.get("persistence"),
            }
        ),
    ]
    return "\n".join(line for line in prompt_lines if line is not None).strip()


def _previous_rejected_attempts_section(
    previous_rejected_attempts: Optional[List[Dict[str, Any]]],
) -> List[str]:
    if not previous_rejected_attempts:
        return []

    lines = [
        "INTENTOS ANTERIORES RECHAZADOS EN ESTA MISMA REPARACIÓN",
        (
            "Ya intentaste corregir este archivo antes en esta misma reparación y cada intento "
            "fue rechazado por introducir un error NUEVO. NO repitas el mismo contenido ni el "
            "mismo error otra vez; corrige de una forma distinta."
        ),
    ]
    for index, attempt in enumerate(previous_rejected_attempts, start=1):
        issues = attempt.get("issues") or []
        lines.append(f"Intento rechazado #{index}:")
        lines.append("- Errores nuevos que introdujo ese intento:")
        if issues:
            lines.extend(f"  - {issue}" for issue in issues)
        else:
            lines.append("  - (sin detalle)")
        lines.append("- Contenido rechazado de ese intento:")
        lines.append(str(attempt.get("content") or ""))
        lines.append("")
    return lines


def build_prompt_file_contract_fix(
    *,
    spec: dict,
    file_contract: dict,
    previous_content: str,
    errors: List[str],
    related_contracts: List[dict],
    structured_issues: Optional[List[Dict[str, Any]]] = None,
    generated_interface_summaries: Optional[List[Dict[str, Any]]] = None,
    previous_rejected_attempts: Optional[List[Dict[str, Any]]] = None,
) -> str:
    fc_path = (file_contract.get("path") or "").replace("\\", "/")
    related_summary = _summarize_related_contracts(related_contracts)
    prompt_lines = [
        "TAREA",
        "Repara EXACTAMENTE UN (1) archivo según el FileContract. Corrige errores y devuelve el archivo completo.",
        "",
        "REGLAS DE SALIDA (OBLIGATORIAS)",
        "- Devuelve SOLO JSON válido, sin texto extra.",
        "- PROHIBIDO usar ``` o markdown.",
        "- Devuelve EXACTAMENTE:",
        "{",
        f'  "path": "{fc_path}",',
        '  "content": "..."',
        "}",
        "- PROHIBIDO cambiar el path.",
        "- PROHIBIDO generar otros archivos.",
        "",
        "REGLAS DE REPARACIÓN",
        "- Corrige exactamente las violaciones indicadas.",
        "- No elimines obligaciones ya implementadas para resolver un error aislado.",
        "- No conviertas una integración real en pass, TODO, fake o NotImplementedError.",
        "- Mantén todas las acciones required=true.",
        "- Corrige únicamente el archivo actual.",
        "- No cambies otros archivos.",
        "- No modifiques el contrato.",
        "- No añadas nuevos campos de configuración.",
        "- No cambies authentication constraints.",
        "- No inventes configuración adicional para justificar una auth inválida.",
        "- Rewrite the implementation when auth is invalid; do not expand config contracts.",
        "- Preserve all unrelated behavior.",
        "- Cada provided_interface debe existir como símbolo Python real.",
        "- No renombres interfaces ofrecidas por otros archivos.",
        "- No cambies el nombre de ningún símbolo declarado.",
        "- Cada required_internal_call required=true debe importarse e invocarse.",
        "- No dupliques en el consumidor la implementación del proveedor.",
        "- No sustituyas una llamada requerida por una respuesta constante.",
        "- Si el consumidor llama a un símbolo incorrecto, usa el símbolo contractual existente.",
        "- Si el archivo proveedor carece de un símbolo contractual, impleméntalo con el nombre exacto.",
        "",
        *_priority_rules(),
        "",
        "REGLAS ESPECÍFICAS DEL KIND",
        *_kind_specific_rules(file_contract),
        "",
        *_configuration_contract_section(file_contract),
        "",
        *_authentication_contract_section(file_contract),
        "",
        "FILE CONTRACT (ACTUAL, FUENTE DE VERDAD)",
        _safe_json(file_contract),
        "",
        "CONTRATOS RELACIONADOS (RESUMEN INTER-ARCHIVO)",
        _safe_json(related_summary),
        "",
        "VIOLACIONES ESTRUCTURADAS",
        _safe_json(structured_issues or []),
        "",
        "REPAIR CONTEXT",
        _safe_json(
            {
                "configuration_contract": file_contract.get("configuration_access")
                or {},
                "authentication_constraints": file_contract.get(
                    "authentication_constraints"
                )
                or {},
                "authentication_runtime_contract": file_contract.get(
                    "authentication_runtime_contract"
                )
                or {},
                "invalid_configuration_fields": [
                    issue.get("details", {}).get("field")
                    for issue in (structured_issues or [])
                    if isinstance(issue, dict)
                    and issue.get("code") == "CONFIGURATION_FIELD_MISSING"
                    and isinstance(issue.get("details"), dict)
                    and issue.get("details", {}).get("field")
                ],
                "allowed_configuration_fields": sorted(
                    {
                        str(field).strip()
                        for issue in (structured_issues or [])
                        if isinstance(issue, dict)
                        and issue.get("code") == "CONFIGURATION_FIELD_MISSING"
                        and isinstance(issue.get("details"), dict)
                        for field in (issue.get("details", {}).get("allowed_fields") or [])
                        if str(field).strip()
                    }
                ),
            }
        ),
        "",
        "CONFIGURATION FIELD REPAIR RULES",
        "The current implementation references a configuration field that is not allowed by the FileContract.",
        "Invalid field(s):",
        *(
            [
                f"- {issue.get('details', {}).get('field')}"
                for issue in (structured_issues or [])
                if isinstance(issue, dict)
                and issue.get("code") == "CONFIGURATION_FIELD_MISSING"
                and isinstance(issue.get("details"), dict)
                and issue.get("details", {}).get("field")
            ]
            or ["- []"]
        ),
        "Allowed fields:",
        *(
            [
                f"- {field}"
                for field in sorted(
                    {
                        str(field).strip()
                        for issue in (structured_issues or [])
                        if isinstance(issue, dict)
                        and issue.get("code") == "CONFIGURATION_FIELD_MISSING"
                        and isinstance(issue.get("details"), dict)
                        for field in (issue.get("details", {}).get("allowed_fields") or [])
                        if str(field).strip()
                    }
                )
            ]
            or ["- []"]
        ),
        "Rewrite the target file so that:",
        "1. The invalid configuration field is completely removed.",
        "2. No replacement configuration field is invented.",
        "3. Only allowed configuration fields are referenced.",
        "4. Authentication still complies with the runtime authentication contract.",
        "5. Do not modify config.py.",
        "6. Do not modify SPEC or FileContracts.",
        "7. Preserve unrelated valid behavior.",
        "",
        "ERRORES A CORREGIR",
        "- " + "\n- ".join(errors or ["repair_file"]),
        "",
        *_previous_rejected_attempts_section(previous_rejected_attempts),
        "INTERFACES REALES DISPONIBLES",
        _safe_json(generated_interface_summaries or []),
        "",
        "CONTENIDO ANTERIOR (REFERENCIA)",
        previous_content,
        "",
        "SPEC (RESUMEN; REFERENCIA SECUNDARIA)",
        _safe_json(_spec_summary(spec)),
    ]
    return "\n".join(line for line in prompt_lines if line is not None).strip()
