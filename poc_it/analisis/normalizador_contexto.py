"""
PoC-it - Normalizador de Contexto

Responsabilidad:
- Recibir una PlantillaUsuario rellenada.
- Convertirla en un contexto técnico estructurado y normalizado.
- Reducir ambigüedad narrativa.
- Servir como única fuente formal de contexto para el sistema.

Este módulo NO genera código.
Solo estructura y formaliza el contexto.

Cambio clave (refactor):
- Separar estrictamente contratos API explícitos vs propuestos:
  - contratos_api_explicitos: requieren evidence literal.
  - contratos_api_propuestos: requieren assumption (no evidence literal).
- Evitar que endpoints propuestos se consuman como fuente de verdad.
- Mantener compatibilidad temporal con contratos_api legacy (deprecated):
  - Se deriva SOLO de contratos_api_explicitos.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from poc_it.infraestructura.llm_client import chat_completion_json
from poc_it.modulos.models import PlantillaUsuario


PROMPT_NORMALIZACION = """
Actúa como arquitecto software senior. Convierte una plantilla de PoC en un contexto técnico estructurado.

Devuelve ÚNICAMENTE JSON válido con la siguiente estructura:

{
  "objetivo_tecnico": "string",
  "actores_principales": ["string"],
  "funcionalidades_clave": ["string"],
  "integraciones_externas": ["string"],
  "restricciones_tecnicas": ["string"],
  "requisitos_no_funcionales": ["string"],
  "riesgos_inherentes": ["string"],
  "complejidad_inferida": "BAJA | MEDIA | ALTA | CRITICA",

  "integrations": [
    {
      "id": "string estable en snake_case",
      "name": "string",
      "kind": "external_api | database | queue | cache | object_storage | email | auth | observability | runtime | other",
      "role": "string",
      "required": true,
      "implementation_level": "fully_local | integration_skeleton | mocked | documentation_only",
      "authentication": {
        "mechanism": "string",
        "credential_source": "runtime | file | environment | request | unknown",
        "allows_embedded_secret": false,
        "allows_static_credential_file": true,
        "source": "explicit | inferred | default | unknown",
        "evidence": "string",
        "assumption": "string"
      },
      "technology_refs": ["string"],
      "configuration_refs": ["string"],
      "source": "explicit | inferred | default | unknown",
      "evidence": "string",
      "assumption": "string"
    }
  ],
  "configuration": [
    {
      "key": "string",
      "purpose": "string",
      "required": true,
      "secret": false,
      "source": "explicit | inferred | default | unknown",
      "evidence": "string",
      "assumption": "string"
    }
  ],

  "contratos_api_explicitos": [
    {
      "method": "GET | POST | PUT | PATCH | DELETE",
      "path": "/ruta",
      "request": {"type": "json | multipart | query | none", "schema_hint": {}, "evidence": "cita literal"},
      "response": {"json_example": {}, "evidence": "cita literal"},
      "notes": "string opcional",
      "actions": [
        {
          "id": "string estable en snake_case",
          "kind": "internal_processing | persistence | external_call | validation | transformation | notification | other",
          "description": "string",
          "required": true,
          "integration_ref": "id de integración o null",
          "source": "explicit | inferred | default | unknown",
          "evidence": "string",
          "assumption": "string"
        }
      ],
      "errors": [
        {
          "status_code": 401,
          "code": "string estable en snake_case",
          "description": "string",
          "required": true,
          "source": "explicit | inferred | default | unknown",
          "evidence": "string",
          "assumption": "string"
        }
      ],
      "integration_refs": ["string"]
    }
  ],
  "contratos_api_propuestos": [
    {
      "method": "GET | POST | PUT | PATCH | DELETE",
      "path": "/ruta",
      "request": {"type": "json | multipart | query | none", "schema_hint": {}, "assumption": "por qué se propone"},
      "response": {"json_example": {}},
      "notes": "string opcional",
      "actions": [
        {
          "id": "string estable en snake_case",
          "kind": "internal_processing | persistence | external_call | validation | transformation | notification | other",
          "description": "string",
          "required": true,
          "integration_ref": "id de integración o null",
          "source": "explicit | inferred | default | unknown",
          "evidence": "string",
          "assumption": "string"
        }
      ],
      "errors": [
        {
          "status_code": 401,
          "code": "string estable en snake_case",
          "description": "string",
          "required": true,
          "source": "explicit | inferred | default | unknown",
          "evidence": "string",
          "assumption": "string"
        }
      ],
      "integration_refs": ["string"]
    }
  ],

  "capability_coverage": [
    {
      "capability_id": "stable_identifier",
      "capability": "descripción de la capacidad",
      "contract_refs": ["POST /upload"],
      "action_refs": ["POST /upload#external_action_id"],
      "integration_refs": ["external_system"],
      "status": "covered | partially_covered | uncovered | not_api_applicable",
      "source": "explicit | inferred | unknown",
      "evidence": "string",
      "assumption": "string"
    }
  ],

  "persistence": {
    "required": false,
    "kind": null,
    "durable_state": false,
    "business_entities": [],
    "evidence": [],
    "uncertainty": ""
  },

  "technology_signals": [
    {
      "name": "string",
      "category": "framework | persistence | cache | queue | object_storage | search | external_api | auth | observability | runtime | library | unknown",
      "packages": ["string"],
      "import_roots": ["string"],
      "role": "string",
      "evidence": "cita literal",
      "confidence": "explicit | inferred | unknown"
    }
  ],

  "domain_entities": [
    {
      "name": "string",
      "singular": "string",
      "plural": "string",
      "evidence": "cita literal",
      "confidence": "explicit | inferred | unknown"
    }
  ],

  "operation_groups": [
    {
      "type": "crud",
      "entity": "string",
      "evidence": "cita literal",
      "confidence": "explicit | inferred | unknown"
    }
  ],

  "state_requirements": {
    "durable": false,
    "entities": [],
    "evidence": []
  },

  "assumptions": ["string"],
  "evidence": ["string"]
}

Reglas:
- No inventes endpoints explícitos si no hay evidencia literal.
- No inventar no significa dejar campos vacíos: extrae todas las funcionalidades, restricciones, tecnologías, persistencia y necesidades de estado que estén evidenciadas en la plantilla.
- Extrae domain_entities y operation_groups cuando exista evidencia literal; si no, deja vacío.
- Todo endpoint explícito debe incluir evidence (request.evidence y/o response.evidence) con cita literal.
- Todo endpoint propuesto debe incluir request.assumption.
- Conserva como explicit cualquier acción, error, tecnología, autenticación o configuración que el usuario haya solicitado literalmente.
- No inventes errores explícitos.
- Si el usuario proporciona un código HTTP concreto, consérvalo.
- Si el usuario no especifica errores, deja errors vacío o añade solo propuestas marcadas como inferred.
- Toda acción que llame a un sistema externo debe usar kind="external_call" y referenciar una integración mediante integration_ref.
- integration_skeleton significa que debe generarse la base de la integración, aunque no pueda verificarse con credenciales o recursos reales.
- No inventes valores reales de secretos, IDs de carpetas, URLs privadas ni credenciales.
- Puedes proponer una variable de configuración cuando sea imprescindible para implementar una integración, pero debes marcarla como inferred.
- evidence debe contener una cita o fragmento respaldado por la plantilla cuando source="explicit".
- assumption debe explicar la inferencia cuando source!="explicit".

Reglas de cobertura funcional y contratos API:
- Analiza cada funcionalidad solicitada por el usuario de manera independiente.
- Una misma plantilla puede contener una combinación de:
  1. contratos API explícitos, con método y ruta literales;
  2. funcionalidades que requieren una interfaz API, pero sin método o ruta literal.
- Cuando aparezcan literalmente un método HTTP y una ruta, por ejemplo "POST /upload" o "GET /health", crea un contrato en contratos_api_explicitos y conserva como evidence el fragmento literal.
- Cuando una funcionalidad obligatoria requiera una operación accesible a través de la API, pero el usuario no proporcione método o ruta, crea un contrato en contratos_api_propuestos. Elige un método y una ruta mínimos y coherentes, e incluye una assumption que explique exactamente la inferencia.
- No descartes contratos propuestos porque existan contratos explícitos. contratos_api_explicitos y contratos_api_propuestos pueden contener elementos simultáneamente.
- Cada funcionalidad obligatoria debe quedar vinculada, cuando corresponda, a: un contrato API, una acción dentro de un contrato o una integración.
- No conviertas una funcionalidad en un endpoint cuando pueda implementarse únicamente como comportamiento interno de otro endpoint.
- No inventes operaciones ajenas a las capacidades solicitadas.

Ejemplo mixto:
Entrada:
"Debe exponer GET /health. Además, debe permitir almacenar un documento en un sistema externo mediante una integración autenticada."

Salida conceptual:
- GET /health → contratos_api_explicitos
- POST /documents → contratos_api_propuestos
- Acción external_call hacia la integración externa correspondiente
- Integración externa correspondiente
- Assumption que indique que POST /documents se propone porque el usuario pidió la capacidad, pero no especificó su interfaz.

Reglas capability_coverage:
- CORRESPONDENCIA OBLIGATORIA 1:1 con funcionalidades_clave: capability_coverage debe tener EXACTAMENTE un elemento por cada elemento de funcionalidades_clave, en el mismo orden. Ni más (no dividas una funcionalidad en varias entradas), ni menos (ninguna funcionalidad puede quedar sin su entrada de cobertura).
- El campo "capability" debe ser una copia LITERAL, carácter a carácter, del string correspondiente en funcionalidades_clave. Nunca lo parafrasees, resumas ni lo dividas en sub-capacidades, aunque esa funcionalidad implique varios contratos/acciones (por ejemplo, un CRUD completo sigue siendo UNA sola funcionalidad si así se declaró en funcionalidades_clave): en ese caso, lista TODOS los contratos/acciones relevantes dentro de contract_refs/action_refs de esa misma entrada, no crees una entrada por cada contrato.
- capability_id puede utilizarse como identificador descriptivo interno de la respuesta del normalizador, pero la aplicación asignará posteriormente el identificador canónico de cada capacidad durante la reconciliación.
- No dependas de capability_id para expresar la relación entre una capacidad y sus contratos.
- contract_refs, action_refs e integration_refs deben contener las referencias estructurales necesarias para vincular cada capacidad; una misma entrada puede (y debe, cuando aplique) listar varios contratos/acciones.
- No marques covered sin referencias válidas.
- Las referencias deben existir después de construir RequestIR.
- Una capacidad de integración no está cubierta solo porque aparezca el nombre de una librería.
- Una capacidad que requiera interactuar con un sistema externo debe referenciar una acción de tipo external_call y la integración correspondiente.
- Una capacidad de persistencia en una base de datos propia (integración kind="database") debe referenciar una acción de tipo persistence, no external_call: external_call es solo para servicios de terceros genuinamente externos (APIs, colas, email, etc.), nunca para el acceso a la base de datos propia de la PoC.
- No determines que una capacidad es externa basándote únicamente en verbos como subir, enviar, guardar, publicar o consultar. Debes determinarlo por la participación real de una integración externa.
- Ejemplo (nótese que "capability" es idéntico, literal, al elemento de funcionalidades_clave):
  {
    "funcionalidades_clave": [
      "Enviar una notificación mediante POST /notifications"
    ],
    "capability_coverage": [
      {
        "capability_id": "send_notification",
        "capability": "Enviar una notificación mediante POST /notifications",
        "contract_refs": [
          "POST /notifications"
        ],
        "action_refs": [
          "POST /notifications#send_notification"
        ],
        "integration_refs": [
          "notification_provider"
        ],
        "status": "covered"
      }
    ]
  }
- Ejemplo de funcionalidad que agrupa varios contratos (CRUD) en UNA sola entrada de cobertura:
  {
    "funcionalidades_clave": [
      "Gestionar productos mediante operaciones de creación, consulta, modificación y eliminación"
    ],
    "capability_coverage": [
      {
        "capability_id": "manage_products",
        "capability": "Gestionar productos mediante operaciones de creación, consulta, modificación y eliminación",
        "contract_refs": [
          "POST /products",
          "GET /products/{product_id}",
          "PUT /products/{product_id}",
          "DELETE /products/{product_id}"
        ],
        "action_refs": [
          "POST /products#create_product",
          "GET /products/{product_id}#get_product",
          "PUT /products/{product_id}#update_product",
          "DELETE /products/{product_id}#delete_product"
        ],
        "integration_refs": [
          "products_database"
        ],
        "status": "covered"
      }
    ]
  }
- Ejemplos:
  - Enviar una notificación mediante un proveedor externo → requiere integración y external_call.
  - Publicar un mensaje en un broker → requiere integración y external_call.
  - Guardar un documento en un almacenamiento externo → requiere integración y external_call.
  - Procesar internamente un fichero recibido → puede ser una acción interna sin integración externa.
- El verbo utilizado no determina por sí mismo la externalidad.
- GET /health no cubre otras capacidades por el mero hecho de garantizar arrancabilidad.

Reglas persistence/state:
- persistence.required=true SOLO si el usuario pide conservar estado de negocio durable entre peticiones/sesiones.
- No activar persistence.required solo por mencionar una tecnología.
- kind NO es vendor-specific. Valores: relational | document | key_value | object_storage | event_log | unknown | null.
- Si hay duda: required=false y usar uncertainty/assumptions.

Reglas technology_signals:
- Extrae tecnologías mencionadas explícitamente por el usuario (no inventar).
- Cada señal debe incluir evidence literal y confidence.
- Si no puedes clasificar con seguridad: category="unknown", confidence="unknown".
- packages: lista de nombres de paquetes que deben instalarse mediante el package manager del lenguaje. Si la tecnología no corresponde a un paquete instalable, devolver [].
- import_roots: lista de módulos/import roots que el código puede importar gracias a esos paquetes. Si no aplica, devolver [].
- No deduzcas packages por category, por nombre ni por heurísticas de proveedor.

Reglas authentication:
- credential_source describe de dónde se obtienen las credenciales.
- allows_embedded_secret indica si el requisito permite secretos literales en código.
- allows_static_credential_file indica si el requisito permite depender de un fichero estático de credenciales.
- No derives estas propiedades a partir del nombre del mecanismo; decláralas explícitamente.

Ejemplos genéricos:
- Librería instalable:
  {
    "name": "external-client-sdk",
    "category": "library",
    "packages": ["external-client-sdk"],
    "import_roots": ["external_client"]
  }
- Mecanismo no instalable:
  {
    "name": "runtime identity",
    "category": "auth",
    "packages": [],
    "import_roots": []
  }
- Runtime/plataforma no instalable:
  {
    "name": "serverless runtime",
    "category": "runtime",
    "packages": [],
    "import_roots": []
  }

No hagas obligatorios integrations, configuration, actions, errors ni integration_refs: usa listas vacías cuando no apliquen.

No añadas texto fuera del JSON.
"""

_HTTP_METHOD_PATH_RE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_./{}:-]*)",
    re.IGNORECASE,
)
_HTTP_CONTRACT_RE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_./{}:-]*)",
    re.IGNORECASE,
)
_INTERNAL_NORMALIZER_KEYS = {
    "_deprecated",
    "_debug",
}


def prepare_contexto_normalizado_payload(
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Elimina metadatos internos de la salida del normalizador antes
    de validarla mediante ContextoNormalizado.

    No elimina campos funcionales desconocidos: esos deben seguir
    provocando un error con extra='forbid'.
    """
    return {
        key: value
        for key, value in data.items()
        if key not in _INTERNAL_NORMALIZER_KEYS
    }


def normalizar_plantilla(plantilla: PlantillaUsuario) -> Dict[str, Any]:
    """
    Normaliza la plantilla del usuario en un contexto técnico estructurado.

    Importante:
    - Este normalizador NO debe mezclar propuestas con contratos explícitos.
    - Si el LLM falla o responde parcialmente, el resultado debe seguir siendo seguro:
      no inventar endpoints explícitos.
    """
    prompt = f"""
{PROMPT_NORMALIZACION}

Plantilla proporcionada por el usuario:

Nombre: {plantilla.nombre}
Problema: {plantilla.problema}
Usuarios: {plantilla.usuarios}
Funcionalidades: {plantilla.funcionalidades}
Límites: {plantilla.limites}
Tecnologías declaradas: {plantilla.tecnologias}
"""

    respuesta = chat_completion_json(
        prompt=prompt,
        system="Responde exclusivamente con JSON válido.",
        temperature=0.0,
        max_tokens=4000,
        fase="normalizacion_contexto",
    )

    _persist_debug_artifact_text("normalizer_raw_response", respuesta)

    try:
        data = json.loads(respuesta)
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    _persist_debug_artifact_json("normalizer_parsed", data)

    data = _ensure_normalized_shape(data, plantilla)

    _apply_deterministic_fallbacks(data, plantilla)
    _persist_debug_artifact_json("normalizer_after_fallbacks", data)

    # Sanitización fuerte para invariantes explícito/propuesto
    _sanitize_contracts_inplace(data)
    _reconcile_integrations_inplace(data, plantilla)
    _reconcile_capability_coverage_inplace(data)

    sanitized_signals: List[Dict[str, Any]] = []
    for signal in _normalize_technology_signals(data.get("technology_signals")):
        if str(signal.get("evidence") or "").strip():
            sanitized_signals.append(signal)

    technology_signals, technology_collisions = canonicalize_technology_signals(
        sanitized_signals
    )
    integrations, technology_trace = canonicalize_integration_technology_refs(
        data.get("integrations") or [],
        technology_signals,
    )
    data["technology_signals"] = technology_signals
    data["integrations"] = integrations

    normalizer_debug = data.get("_debug") if isinstance(data.get("_debug"), dict) else {}
    normalizer_debug["technology_ref_reconciliation"] = technology_trace
    if technology_collisions:
        normalizer_debug["technology_id_collisions"] = technology_collisions
        for collision in technology_collisions:
            _append_open_question(
                data,
                f"TECHNOLOGY_ID_COLLISION: {collision.get('technology_id', '')}",
            )
    data["_debug"] = normalizer_debug

    _persist_debug_artifact_json("technology_ref_reconciliation", technology_trace)
    _persist_debug_artifact_json("normalizer_sanitized", data)

    # Compatibilidad temporal (deprecated): contratos_api legacy SOLO explícitos
    data["contratos_api"] = list(data.get("contratos_api_explicitos") or [])
    data["_deprecated"] = {"contratos_api": "Use contratos_api_explicitos/contratos_api_propuestos"}

    # enriquecer trazabilidad global sin mezclar con contratos
    data["evidence"] = _dedupe_str_list(
        list(data.get("evidence") or []) + _template_evidence_lines(plantilla)
    )
    data["assumptions"] = _dedupe_str_list(list(data.get("assumptions") or []))

    coverage_report = _build_capability_coverage_report(data)
    _persist_debug_artifact_json("capability_coverage", coverage_report)

    _persist_debug_context(plantilla, data)
    return data


def _apply_deterministic_fallbacks(data: Dict[str, Any], plantilla: PlantillaUsuario) -> None:
    """
    Fallback determinista mínimo para cuando el LLM devuelve JSON pobre/empty.

    Principios:
    - NO inferir desde texto libre salvo extracción literal determinista de método+ruta.
    - Solo estructurar campos explícitos de la plantilla (funcionalidades/limites/tecnologias).
    - No activar persistencia ni proponer endpoints si no hay evidencia estructurada en el JSON del LLM.
    """
    if not isinstance(data, dict):
        return

    data["funcionalidades_clave"] = _extract_functionalities_from_template(
        plantilla, existing=data.get("funcionalidades_clave")
    )
    data["restricciones_tecnicas"] = _extract_constraints_from_template(
        plantilla, existing=data.get("restricciones_tecnicas")
    )

    if not (isinstance(data.get("technology_signals"), list) and data.get("technology_signals")):
        data["technology_signals"] = _extract_technology_signals_from_template(plantilla)

    data["persistence"] = _extract_persistence_from_template(
        plantilla, existing=data.get("persistence"), technology_signals=data.get("technology_signals")
    )

    recovered_explicit = _extract_explicit_api_contracts_from_template(plantilla)
    current_explicit = (
        data.get("contratos_api_explicitos")
        if isinstance(data.get("contratos_api_explicitos"), list)
        else []
    )
    data["contratos_api_explicitos"] = _merge_contract_dicts(
        current_explicit,
        recovered_explicit,
    )

    _propose_api_contracts_from_crud_if_evidenced(data)


def _extract_explicit_api_contracts_from_template(
    plantilla: PlantillaUsuario,
) -> List[Dict[str, Any]]:
    sources = [
        plantilla.problema or "",
        plantilla.funcionalidades or "",
        plantilla.limites or "",
    ]

    contracts: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for source_text in sources:
        for match in _HTTP_METHOD_PATH_RE.finditer(source_text):
            method = match.group(1).upper()
            path = match.group(2).rstrip(".,;:)")
            if not path.startswith("/"):
                continue
            key = (method, _normalize_path_key(path))
            if key in seen:
                continue
            seen.add(key)

            evidence = match.group(0).strip()
            contracts.append(
                {
                    "method": method,
                    "path": path,
                    "request": {
                        "type": "none",
                        "schema_hint": {},
                        "evidence": evidence,
                    },
                    "response": {
                        "json_example": {},
                        "evidence": evidence,
                    },
                    "notes": (
                        "Contrato recuperado determinísticamente desde "
                        "método y ruta literales de la plantilla."
                    ),
                    "actions": [],
                    "errors": [],
                    "integration_refs": [],
                }
            )

    return contracts


def _merge_contract_dicts(
    primary: List[Any],
    secondary: List[Any],
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    index_by_key: Dict[Tuple[str, str], int] = {}

    def _register(item: Any) -> None:
        if not isinstance(item, dict):
            return
        normalized = _normalize_contract_shape(item)
        method = str(normalized.get("method") or "").upper().strip()
        path = _normalize_path_key(str(normalized.get("path") or ""))
        if not method or not path:
            return
        key = (method, path)
        if key not in index_by_key:
            index_by_key[key] = len(merged)
            merged.append(normalized)
            return
        merged[index_by_key[key]] = _merge_single_contract_dicts(
            merged[index_by_key[key]],
            normalized,
        )

    for item in primary or []:
        _register(item)
    for item in secondary or []:
        _register(item)

    return merged


def _merge_single_contract_dicts(
    preferred: Dict[str, Any],
    fallback: Dict[str, Any],
) -> Dict[str, Any]:
    out = _normalize_contract_shape(preferred)
    fb = _normalize_contract_shape(fallback)

    req_out = out.get("request") if isinstance(out.get("request"), dict) else {}
    req_fb = fb.get("request") if isinstance(fb.get("request"), dict) else {}
    resp_out = out.get("response") if isinstance(out.get("response"), dict) else {}
    resp_fb = fb.get("response") if isinstance(fb.get("response"), dict) else {}

    if not _coalesce_str(req_out.get("evidence"), "") and _coalesce_str(req_fb.get("evidence"), ""):
        req_out = dict(req_out)
        req_out["evidence"] = _coalesce_str(req_fb.get("evidence"), "")
    if not _coalesce_str(resp_out.get("evidence"), "") and _coalesce_str(resp_fb.get("evidence"), ""):
        resp_out = dict(resp_out)
        resp_out["evidence"] = _coalesce_str(resp_fb.get("evidence"), "")

    if not isinstance(req_out.get("schema_hint"), dict) and isinstance(req_fb.get("schema_hint"), dict):
        req_out = dict(req_out)
        req_out["schema_hint"] = dict(req_fb.get("schema_hint") or {})
    elif isinstance(req_out.get("schema_hint"), dict) and isinstance(req_fb.get("schema_hint"), dict):
        merged_schema = dict(req_out.get("schema_hint") or {})
        for key, value in dict(req_fb.get("schema_hint") or {}).items():
            merged_schema.setdefault(key, value)
        req_out = dict(req_out)
        req_out["schema_hint"] = merged_schema

    if not out.get("actions") and fb.get("actions"):
        out["actions"] = list(fb.get("actions") or [])
    if not out.get("errors") and fb.get("errors"):
        out["errors"] = list(fb.get("errors") or [])
    if not out.get("integration_refs") and fb.get("integration_refs"):
        out["integration_refs"] = list(fb.get("integration_refs") or [])
    if not _coalesce_str(out.get("notes"), "") and _coalesce_str(fb.get("notes"), ""):
        out["notes"] = _coalesce_str(fb.get("notes"), "")

    out["request"] = req_out
    out["response"] = resp_out
    return out


def _extract_functionalities_from_template(plantilla: PlantillaUsuario, existing: Any) -> List[str]:
    if isinstance(existing, list) and any(isinstance(x, str) and x.strip() for x in existing):
        return _ensure_list_of_str(existing)

    txt = (plantilla.funcionalidades or "").strip()
    return [txt] if txt else []


def _extract_constraints_from_template(plantilla: PlantillaUsuario, existing: Any) -> List[str]:
    if isinstance(existing, list) and any(isinstance(x, str) and x.strip() for x in existing):
        return _ensure_list_of_str(existing)

    txt = (plantilla.limites or "").strip()
    return [txt] if txt else []


def _extract_technology_signals_from_template(plantilla: PlantillaUsuario) -> List[Dict[str, Any]]:
    raw = (plantilla.tecnologias or "").strip()
    if not raw:
        return []

    evidence = f"Tecnologías declaradas: {raw}"
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: List[Dict[str, Any]] = []
    for name in parts:
        out.append(
            {
                "name": name,
                "category": "unknown",
                "packages": [],
                "import_roots": [],
                "role": "",
                "evidence": evidence,
                "confidence": "explicit",
            }
        )
    return _normalize_technology_signals(out)


def _extract_persistence_from_template(
    plantilla: PlantillaUsuario, existing: Any, technology_signals: Any
) -> Dict[str, Any]:
    if isinstance(existing, dict) and existing.get("required") is True:
        ev = _ensure_list_of_str(existing.get("evidence"))
        if ev:
            return existing

    base_uncertainty = ""
    if isinstance(existing, dict) and isinstance(existing.get("uncertainty"), str):
        base_uncertainty = existing.get("uncertainty", "").strip()

    return {
        "required": False,
        "kind": None,
        "durable_state": False,
        "business_entities": [],
        "evidence": [],
        "uncertainty": base_uncertainty or "persistence_not_structured_from_normalizer",
    }


def _propose_api_contracts_from_crud_if_evidenced(data: Dict[str, Any]) -> None:
    if not isinstance(data.get("contratos_api_explicitos"), list):
        data["contratos_api_explicitos"] = []
    if not isinstance(data.get("contratos_api_propuestos"), list):
        data["contratos_api_propuestos"] = []

    if data["contratos_api_propuestos"]:
        return

    ops = data.get("operation_groups")
    entities = data.get("domain_entities")
    if not isinstance(ops, list) or not isinstance(entities, list):
        return

    crud_ops = [
        op
        for op in ops
        if isinstance(op, dict)
        and str(op.get("type", "")).strip().lower() == "crud"
        and isinstance(op.get("entity"), str)
        and op.get("entity", "").strip()
        and isinstance(op.get("evidence"), str)
        and op.get("evidence", "").strip()
    ]
    if not crud_ops:
        return

    ent_by_key: Dict[str, Dict[str, Any]] = {}
    for e in entities:
        if not isinstance(e, dict):
            continue
        ev = e.get("evidence") if isinstance(e.get("evidence"), str) else ""
        if not ev.strip():
            continue
        name = e.get("name") if isinstance(e.get("name"), str) else ""
        singular = e.get("singular") if isinstance(e.get("singular"), str) else ""
        plural = e.get("plural") if isinstance(e.get("plural"), str) else ""
        key = (plural or name or singular).strip()
        if not key:
            continue
        ent_by_key[key.lower()] = e

    if not ent_by_key:
        return

    assumptions = data.get("assumptions")
    if not isinstance(assumptions, list):
        assumptions = []
        data["assumptions"] = assumptions

    for op in crud_ops:
        ent_key = op.get("entity", "").strip().lower()
        ent = ent_by_key.get(ent_key)
        if not ent:
            continue

        slug_source = (ent.get("plural") or ent.get("name") or ent.get("singular") or "").strip()
        slug = _slugify_path_segment(slug_source)
        if not slug:
            continue

        assumption = f"Endpoints propuestos derivados de operation_groups(type=crud) para entidad '{slug_source}'."
        assumptions.append(assumption)

        proposed = [
            {
                "method": "POST",
                "path": f"/{slug}",
                "request": {"type": "json", "schema_hint": {}, "assumption": assumption},
                "response": {"json_example": {}},
                "notes": "",
                "actions": [],
                "errors": [],
                "integration_refs": [],
            },
            {
                "method": "GET",
                "path": f"/{slug}",
                "request": {"type": "none", "schema_hint": {}, "assumption": assumption},
                "response": {"json_example": {}},
                "notes": "",
                "actions": [],
                "errors": [],
                "integration_refs": [],
            },
            {
                "method": "GET",
                "path": f"/{slug}" + "/{id}",
                "request": {"type": "none", "schema_hint": {"path_params": {"id": "integer"}}, "assumption": assumption},
                "response": {"json_example": {}},
                "notes": "",
                "actions": [],
                "errors": [],
                "integration_refs": [],
            },
            {
                "method": "PUT",
                "path": f"/{slug}" + "/{id}",
                "request": {"type": "json", "schema_hint": {}, "assumption": assumption},
                "response": {"json_example": {}},
                "notes": "",
                "actions": [],
                "errors": [],
                "integration_refs": [],
            },
            {
                "method": "PATCH",
                "path": f"/{slug}" + "/{id}",
                "request": {"type": "json", "schema_hint": {}, "assumption": assumption},
                "response": {"json_example": {}},
                "notes": "",
                "actions": [],
                "errors": [],
                "integration_refs": [],
            },
            {
                "method": "DELETE",
                "path": f"/{slug}" + "/{id}",
                "request": {"type": "none", "schema_hint": {"path_params": {"id": "integer"}}, "assumption": assumption},
                "response": {"json_example": {}},
                "notes": "",
                "actions": [],
                "errors": [],
                "integration_refs": [],
            },
        ]
        data["contratos_api_propuestos"] = _merge_contract_dicts(
            data.get("contratos_api_propuestos") or [],
            proposed,
        )
        return


def _ensure_normalized_shape(data: Dict[str, Any], plantilla: PlantillaUsuario) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "objetivo_tecnico": plantilla.problema,
        "actores_principales": [],
        "funcionalidades_clave": [],
        "integraciones_externas": [],
        "restricciones_tecnicas": [],
        "requisitos_no_funcionales": [],
        "riesgos_inherentes": [],
        "complejidad_inferida": "MEDIA",
        "integrations": [],
        "configuration": [],
        "contratos_api_explicitos": [],
        "contratos_api_propuestos": [],
        "capability_coverage": [],
        "persistence": {
            "required": False,
            "kind": None,
            "durable_state": False,
            "business_entities": [],
            "evidence": [],
            "uncertainty": "",
        },
        "technology_signals": [],
        "domain_entities": [],
        "operation_groups": [],
        "state_requirements": {"durable": False, "entities": [], "evidence": []},
        "assumptions": [],
        "evidence": [],
    }

    out: Dict[str, Any] = dict(base)
    out.update({k: v for k, v in data.items() if k in out})

    legacy_contracts = data.get("contratos_api")
    if isinstance(legacy_contracts, list) and legacy_contracts:
        exp, prop, extra_assumptions, extra_evidence = _split_contracts_legacy(legacy_contracts)
        out["contratos_api_explicitos"] = list(out.get("contratos_api_explicitos") or []) + exp
        out["contratos_api_propuestos"] = list(out.get("contratos_api_propuestos") or []) + prop
        out["assumptions"] = list(out.get("assumptions") or []) + extra_assumptions
        out["evidence"] = list(out.get("evidence") or []) + extra_evidence

    for k in (
        "actores_principales",
        "funcionalidades_clave",
        "integraciones_externas",
        "restricciones_tecnicas",
        "requisitos_no_funcionales",
        "riesgos_inherentes",
        "assumptions",
        "evidence",
    ):
        out[k] = _ensure_list_of_str(out.get(k))

    out["contratos_api_explicitos"] = (
        out.get("contratos_api_explicitos") if isinstance(out.get("contratos_api_explicitos"), list) else []
    )
    out["contratos_api_propuestos"] = (
        out.get("contratos_api_propuestos") if isinstance(out.get("contratos_api_propuestos"), list) else []
    )
    out["capability_coverage"] = (
        out.get("capability_coverage") if isinstance(out.get("capability_coverage"), list) else []
    )

    if not isinstance(out.get("integrations"), list):
        out["integrations"] = []
    if not isinstance(out.get("configuration"), list):
        out["configuration"] = []

    out["integrations"] = _normalize_integrations(out.get("integrations"))
    out["configuration"] = _normalize_configuration(out.get("configuration"))
    out["persistence"] = _normalize_persistence(out.get("persistence"))
    out["technology_signals"] = _normalize_technology_signals(out.get("technology_signals"))
    out["domain_entities"] = _normalize_domain_entities(out.get("domain_entities"))
    out["operation_groups"] = _normalize_operation_groups(out.get("operation_groups"))
    out["state_requirements"] = _normalize_state_requirements(out.get("state_requirements"))
    out["capability_coverage"] = _normalize_capability_coverage(out.get("capability_coverage"))

    return out


def _split_contracts_legacy(
    items: List[Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str], List[str]]:
    explicit: List[Dict[str, Any]] = []
    proposed: List[Dict[str, Any]] = []
    assumptions: List[str] = []
    evidence: List[str] = []

    for it in items:
        if not isinstance(it, dict):
            continue
        req = it.get("request") if isinstance(it.get("request"), dict) else {}
        resp = it.get("response") if isinstance(it.get("response"), dict) else {}

        ev_req = req.get("evidence") if isinstance(req.get("evidence"), str) else ""
        ev_resp = resp.get("evidence") if isinstance(resp.get("evidence"), str) else ""
        has_evidence = bool((ev_req or "").strip() or (ev_resp or "").strip())

        normalized_contract = _normalize_contract_shape(it)

        if has_evidence:
            explicit.append(normalized_contract)
            if (ev_req or "").strip():
                evidence.append(ev_req.strip())
            if (ev_resp or "").strip():
                evidence.append(ev_resp.strip())
        else:
            req_norm = (
                normalized_contract.get("request")
                if isinstance(normalized_contract.get("request"), dict)
                else {}
            )
            if not isinstance(req_norm.get("assumption"), str) or not req_norm.get("assumption", "").strip():
                req_norm = dict(req_norm)
                req_norm["assumption"] = (
                    "Endpoint propuesto (legacy) sin evidencia literal; requiere confirmación del usuario."
                )
                normalized_contract = dict(normalized_contract)
                normalized_contract["request"] = req_norm
                assumptions.append(req_norm["assumption"])
            proposed.append(normalized_contract)

    return explicit, proposed, assumptions, evidence


def _sanitize_contracts_inplace(data: Dict[str, Any]) -> None:
    explicitos = data.get("contratos_api_explicitos")
    propuestos = data.get("contratos_api_propuestos")

    if not isinstance(explicitos, list):
        explicitos = []
    if not isinstance(propuestos, list):
        propuestos = []

    fixed_explicitos: List[Dict[str, Any]] = []
    moved_to_propuestos: List[Dict[str, Any]] = []
    assumptions: List[str] = list(data.get("assumptions") or [])
    evidence: List[str] = list(data.get("evidence") or [])

    for it in explicitos:
        if not isinstance(it, dict):
            continue

        normalized_contract = _normalize_contract_shape(it)
        req = normalized_contract.get("request") if isinstance(normalized_contract.get("request"), dict) else {}
        resp = normalized_contract.get("response") if isinstance(normalized_contract.get("response"), dict) else {}

        ev_req = req.get("evidence") if isinstance(req.get("evidence"), str) else ""
        ev_resp = resp.get("evidence") if isinstance(resp.get("evidence"), str) else ""
        has_evidence = bool((ev_req or "").strip() or (ev_resp or "").strip())

        if has_evidence:
            fixed_explicitos.append(normalized_contract)
            if (ev_req or "").strip():
                evidence.append(ev_req.strip())
            if (ev_resp or "").strip():
                evidence.append(ev_resp.strip())
        else:
            req2 = dict(req)
            if not isinstance(req2.get("assumption"), str) or not req2.get("assumption", "").strip():
                req2["assumption"] = (
                    "Endpoint sin evidencia literal; se trató como propuesto y requiere confirmación del usuario."
                )
            moved_contract = dict(normalized_contract)
            moved_contract["request"] = req2
            moved_to_propuestos.append(moved_contract)
            assumptions.append(req2["assumption"])

    fixed_propuestos: List[Dict[str, Any]] = []
    for it in list(propuestos) + moved_to_propuestos:
        if not isinstance(it, dict):
            continue
        normalized_contract = _normalize_contract_shape(it)
        req = normalized_contract.get("request") if isinstance(normalized_contract.get("request"), dict) else {}
        req2 = dict(req)
        if not isinstance(req2.get("assumption"), str) or not req2.get("assumption", "").strip():
            req2["assumption"] = "Endpoint propuesto sin evidence literal; requiere confirmación del usuario."
            assumptions.append(req2["assumption"])
        normalized_contract["request"] = req2
        fixed_propuestos.append(normalized_contract)

    data["contratos_api_explicitos"] = _merge_contract_dicts([], fixed_explicitos)
    data["contratos_api_propuestos"] = _merge_contract_dicts([], fixed_propuestos)
    data["assumptions"] = _dedupe_str_list(assumptions)
    data["evidence"] = _dedupe_str_list(evidence)

    data["integrations"] = _normalize_integrations(data.get("integrations"))
    data["configuration"] = _normalize_configuration(data.get("configuration"))
    data["persistence"] = _normalize_persistence(
        data.get("persistence"), assumptions_sink=data["assumptions"]
    )
    data["assumptions"] = _dedupe_str_list(list(data.get("assumptions") or []))
    data["technology_signals"] = _normalize_technology_signals(data.get("technology_signals"))
    data["domain_entities"] = _normalize_domain_entities(data.get("domain_entities"))
    data["operation_groups"] = _normalize_operation_groups(data.get("operation_groups"))
    data["state_requirements"] = _normalize_state_requirements(data.get("state_requirements"))
    data["capability_coverage"] = _normalize_capability_coverage(data.get("capability_coverage"))

    _reconcile_persistence_and_state_requirements(data)
    data["assumptions"] = _dedupe_str_list(list(data.get("assumptions") or []))


def _reconcile_integrations_inplace(data: Dict[str, Any], plantilla: PlantillaUsuario) -> None:
    integrations = _normalize_integrations(data.get("integrations"))
    technology_signals = _normalize_technology_signals(data.get("technology_signals"))

    mentions = _collect_external_mentions(data, plantilla)
    seen_ids = {str(item.get("id") or "").strip().lower(): item for item in integrations}

    for mention in mentions:
        integration_id = str(mention["id"]).strip().lower()
        if integration_id in seen_ids:
            existing = seen_ids[integration_id]
            if not existing.get("technology_refs") and mention.get("technology_ref"):
                existing["technology_refs"] = [mention["technology_ref"]]
            continue
        minimal = _build_minimal_integration_from_mention(mention)
        integrations.append(minimal)
        seen_ids[integration_id] = minimal

    integration_ids = {str(item.get("id") or "").strip().lower() for item in integrations}
    open_questions = list(data.get("open_questions") or [])
    assumptions = list(data.get("assumptions") or [])

    for contracts_key in ("contratos_api_explicitos", "contratos_api_propuestos"):
        contracts = data.get(contracts_key)
        if not isinstance(contracts, list):
            continue
        for contract in contracts:
            if not isinstance(contract, dict):
                continue
            for action in contract.get("actions") or []:
                if not isinstance(action, dict):
                    continue
                if str(action.get("kind") or "").strip().lower() != "external_call":
                    continue
                ref = _coalesce_str(action.get("integration_ref"), "")
                if ref and ref.strip().lower() not in integration_ids:
                    open_questions.append(
                        f"external_call referencia integración inexistente: {ref}"
                    )
            for ref in _ensure_list_of_str(contract.get("integration_refs")):
                if ref.strip().lower() not in integration_ids:
                    open_questions.append(
                        f"Contrato referencia integración inexistente: {ref}"
                    )

    external_integrations = _ensure_list_of_str(data.get("integraciones_externas"))
    for raw_name in external_integrations:
        stable_id = _stable_snake_case_id(raw_name)
        if stable_id.lower() not in integration_ids:
            mention = {
                "id": stable_id,
                "name": raw_name,
                "kind": "other",
                "source": "explicit",
                "evidence": raw_name,
                "technology_ref": "",
            }
            minimal = _build_minimal_integration_from_mention(mention)
            integrations.append(minimal)
            integration_ids.add(stable_id.lower())
            assumptions.append(
                "Se añadió integración mínima para preservar trazabilidad desde integraciones_externas."
            )

    data["integrations"] = _normalize_integrations(integrations)

    technology_trace = (
        data.get("_debug", {}).get("technology_ref_reconciliation")
        if isinstance(data.get("_debug"), dict)
        else None
    )
    for error in _build_technology_ref_open_questions(technology_trace):
        _append_open_question(data, error)

    data["open_questions"] = _dedupe_str_list(open_questions + list(data.get("open_questions") or []))
    data["assumptions"] = _dedupe_str_list(assumptions)


def stable_identifier(
    value: str,
) -> str:
    normalized = unicodedata.normalize(
        "NFKD",
        str(value or ""),
    )
    normalized = "".join(
        char
        for char in normalized
        if not unicodedata.combining(char)
    )
    normalized = normalized.casefold()
    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        normalized,
    )
    return normalized.strip("_")


def canonical_technology_id(
    signal: dict,
) -> str:
    name = str(signal.get("name") or "").strip()
    if name:
        return stable_identifier(name)

    raw_id = str(signal.get("id") or "").strip()
    return stable_identifier(raw_id)


def normalize_alias(
    value: str,
) -> str:
    return str(value or "").strip().casefold()


def canonicalize_technology_signals(
    signals: List[dict],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    result: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    collisions: List[Dict[str, Any]] = []

    for raw in signals:
        if not isinstance(raw, dict):
            continue

        item = dict(raw)
        name = str(item.get("name") or "").strip()
        if not name:
            continue

        canonical_id = canonical_technology_id(item)
        if not canonical_id:
            continue

        if canonical_id in seen_ids:
            collisions.append(
                {
                    "code": "TECHNOLOGY_ID_COLLISION",
                    "technology_id": canonical_id,
                    "name": name,
                }
            )
            continue

        seen_ids.add(canonical_id)
        item["id"] = canonical_id
        item["packages"] = [
            str(value).strip()
            for value in (item.get("packages") or [])
            if str(value).strip()
        ]
        item["import_roots"] = [
            str(value).strip()
            for value in (item.get("import_roots") or [])
            if str(value).strip()
        ]
        result.append(item)

    return result, collisions


def build_technology_alias_index(
    signals: List[dict],
) -> Tuple[Dict[str, str], set[str]]:
    index: Dict[str, str] = {}
    ambiguous: set[str] = set()

    for signal in signals:
        signal_id = str(signal.get("id") or "").strip()
        if not signal_id:
            continue

        aliases: List[str] = [
            signal_id,
            str(signal.get("name") or "").strip(),
        ]
        aliases.extend(
            str(value).strip()
            for value in (signal.get("packages") or [])
            if str(value).strip()
        )
        aliases.extend(
            str(value).strip()
            for value in (signal.get("import_roots") or [])
            if str(value).strip()
        )

        for raw_alias in aliases:
            alias = normalize_alias(raw_alias)
            if not alias:
                continue
            if alias in ambiguous:
                continue

            existing = index.get(alias)
            if existing is not None and existing != signal_id:
                index.pop(alias, None)
                ambiguous.add(alias)
                continue

            index[alias] = signal_id

    return index, ambiguous


def resolve_technology_ref(
    raw_ref: str,
    *,
    alias_index: Dict[str, str],
    ambiguous_aliases: set[str],
) -> Tuple[str | None, str]:
    alias = normalize_alias(raw_ref)
    if not alias:
        return None, "empty"
    if alias in ambiguous_aliases:
        return None, "ambiguous"

    canonical_id = alias_index.get(alias)
    if canonical_id is None:
        return None, "unresolved"
    return canonical_id, "resolved"


def canonicalize_integration_technology_refs(
    integrations: List[dict],
    technology_signals: List[dict],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    alias_index, ambiguous = build_technology_alias_index(technology_signals)

    result: List[Dict[str, Any]] = []
    trace: List[Dict[str, Any]] = []

    for raw_integration in integrations:
        if not isinstance(raw_integration, dict):
            continue

        integration = dict(raw_integration)
        canonical_refs: List[str] = []

        for raw_ref in integration.get("technology_refs") or []:
            canonical_id, status = resolve_technology_ref(
                str(raw_ref),
                alias_index=alias_index,
                ambiguous_aliases=ambiguous,
            )
            trace.append(
                {
                    "integration_id": integration.get("id"),
                    "input_ref": raw_ref,
                    "canonical_id": canonical_id,
                    "status": status,
                }
            )
            if canonical_id is None:
                continue
            if canonical_id not in canonical_refs:
                canonical_refs.append(canonical_id)

        integration["technology_refs"] = canonical_refs
        result.append(integration)

    return result, trace


def _build_technology_signal_index(
    technology_signals: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    canonical_signals, _ = canonicalize_technology_signals(
        _normalize_technology_signals(technology_signals)
    )
    index: Dict[str, Dict[str, Any]] = {}
    for signal in canonical_signals:
        signal_id = str(signal.get("id") or "").strip()
        if signal_id:
            index[signal_id] = signal
    return index


def _technology_identity(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    base = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()
    return stable_identifier(base or raw)


def _build_technology_ref_open_questions(
    trace: Any,
) -> List[str]:
    if not isinstance(trace, list):
        return []

    messages: List[str] = []
    for item in trace:
        if not isinstance(item, dict):
            continue

        status = str(item.get("status") or "").strip().lower()
        if status not in {"unresolved", "ambiguous"}:
            continue

        integration_id = str(item.get("integration_id") or "").strip() or "<unknown_integration>"
        input_ref = str(item.get("input_ref") or "").strip() or "<empty_ref>"

        if status == "ambiguous":
            messages.append(
                "Integración "
                f"{integration_id} referencia alias tecnológico ambiguo: {input_ref}"
            )
            continue

        messages.append(
            "Integración "
            f"{integration_id} referencia tecnología no resuelta: {input_ref}"
        )

    return _dedupe_str_list(messages)


def _validate_integration_technology_refs(
    technology_signals: List[Dict[str, Any]],
    integrations: List[Dict[str, Any]],
) -> List[str]:
    canonical_signals, _ = canonicalize_technology_signals(
        _normalize_technology_signals(technology_signals)
    )
    alias_index, ambiguous_aliases = build_technology_alias_index(canonical_signals)

    errors: List[str] = []

    for integration in integrations:
        if not isinstance(integration, dict):
            continue

        integration_id = str(integration.get("id") or "").strip() or "<unknown_integration>"
        for raw_ref in integration.get("technology_refs") or []:
            canonical_id, status = resolve_technology_ref(
                str(raw_ref),
                alias_index=alias_index,
                ambiguous_aliases=ambiguous_aliases,
            )
            if status == "resolved" and canonical_id is not None:
                continue
            if status == "ambiguous":
                errors.append(
                    f"Integración {integration_id} referencia alias tecnológico ambiguo: {raw_ref}"
                )
                continue
            if status == "empty":
                errors.append(
                    f"Integración {integration_id} referencia tecnología vacía."
                )
                continue
            errors.append(
                f"Integración {integration_id} referencia tecnología inexistente: {raw_ref}"
            )

    return _dedupe_str_list(errors)


def _collect_external_mentions(data: Dict[str, Any], plantilla: PlantillaUsuario) -> List[Dict[str, Any]]:
    mentions: List[Dict[str, Any]] = []

    canonical_signals, _ = canonicalize_technology_signals(
        _normalize_technology_signals(data.get("technology_signals"))
    )

    structured_integrations = _normalize_integrations(data.get("integrations"))
    if structured_integrations:
        return []

    for raw_name in _ensure_list_of_str(data.get("integraciones_externas")):
        mentions.append(
            {
                "id": _stable_snake_case_id(raw_name),
                "name": raw_name,
                "kind": "other",
                "source": "explicit",
                "evidence": raw_name,
                "technology_ref": "",
            }
        )

    inferable_categories = {"external_api"}
    alias_index, ambiguous_aliases = build_technology_alias_index(canonical_signals)

    for signal in canonical_signals:
        category = str(signal.get("category") or "").strip().lower()
        signal_id = str(signal.get("id") or "").strip()
        if category not in inferable_categories or not signal_id:
            continue

        resolved_id, status = resolve_technology_ref(
            signal_id,
            alias_index=alias_index,
            ambiguous_aliases=ambiguous_aliases,
        )
        if status != "resolved" or resolved_id is None:
            continue

        mentions.append(
            {
                "id": signal_id,
                "name": str(signal.get("name") or "").strip() or signal_id,
                "kind": category,
                "source": "explicit"
                if str(signal.get("confidence") or "").strip() == "explicit"
                else "inferred",
                "evidence": str(signal.get("evidence") or "").strip(),
                "technology_ref": resolved_id,
            }
        )

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for mention in mentions:
        key = str(mention.get("id") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(mention)
    return deduped


def _build_minimal_integration_from_mention(mention: Dict[str, Any]) -> Dict[str, Any]:
    source = mention.get("source") if isinstance(mention.get("source"), str) else "inferred"
    evidence = mention.get("evidence") if isinstance(mention.get("evidence"), str) else ""
    technology_ref = mention.get("technology_ref") if isinstance(mention.get("technology_ref"), str) else ""
    return {
        "id": _stable_snake_case_id(str(mention.get("id") or mention.get("name") or "")),
        "name": str(mention.get("name") or "").strip(),
        "kind": str(mention.get("kind") or "other").strip() or "other",
        "role": "Integración necesaria para una capacidad solicitada.",
        "required": True,
        "implementation_level": "integration_skeleton",
        "authentication": {
            "mechanism": "",
            "credential_source": "unknown",
            "allows_embedded_secret": False,
            "allows_static_credential_file": True,
            "source": "unknown",
            "evidence": "",
            "assumption": "",
        },
        "technology_refs": [technology_ref] if technology_ref else [],
        "configuration_refs": [],
        "source": source if source in {"explicit", "inferred"} else "inferred",
        "evidence": evidence,
        "assumption": (
            ""
            if source == "explicit" and evidence
            else "Integración inferida a partir de una capacidad o tecnología externa declarada por el usuario."
        ),
    }


def _normalize_capability_coverage(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    allowed_status = {"covered", "partially_covered", "uncovered", "not_api_applicable"}
    allowed_source = {"explicit", "inferred", "unknown", "derived"}

    out: List[Dict[str, Any]] = []
    seen = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        capability = _coalesce_str(item.get("capability"), "")
        if not capability:
            continue
        capability_id = _coalesce_str(item.get("capability_id"), "")
        status = str(item.get("status") or "uncovered").strip()
        if status not in allowed_status:
            status = "uncovered"
        source = str(item.get("source") or "unknown").strip()
        if source not in allowed_source:
            source = "unknown"
        dedupe_key = capability_id.lower() or f"text::{_normalize_text(capability)}::{index}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(
            {
                "capability_id": capability_id,
                "capability": capability,
                "contract_refs": _dedupe_str_list(_ensure_list_of_str(item.get("contract_refs"))),
                "action_refs": _dedupe_str_list(_ensure_list_of_str(item.get("action_refs"))),
                "integration_refs": _dedupe_str_list(_ensure_list_of_str(item.get("integration_refs"))),
                "status": status,
                "source": source,
                "evidence": _coalesce_str(item.get("evidence"), ""),
                "assumption": _coalesce_str(item.get("assumption"), ""),
            }
        )
    return out


def _stable_capability_id(capability: str, index: int) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (capability or "").strip().lower())
    normalized = normalized.strip("_")
    if not normalized:
        normalized = f"capability_{index + 1}"
    return normalized[:80]


def _assign_capability_ids(capabilities: List[str]) -> List[Dict[str, str]]:
    assigned: List[Dict[str, str]] = []
    counts: Dict[str, int] = {}

    for index, capability in enumerate(capabilities):
        base_id = _stable_capability_id(capability, index)
        counts[base_id] = counts.get(base_id, 0) + 1
        count = counts[base_id]
        capability_id = base_id if count == 1 else f"{base_id}_{count}"
        assigned.append({"id": capability_id, "description": capability})

    return assigned


def _normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _extract_contract_refs_from_capability(
    capability: str,
) -> List[str]:
    refs: List[str] = []

    for match in _HTTP_CONTRACT_RE.finditer(capability or ""):
        method = match.group(1).upper()
        path = match.group(2).rstrip(".,;:)")
        ref = f"{method} {path}"
        if ref not in refs:
            refs.append(ref)

    return refs


def _find_exact_text_coverage(
    capability: str,
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    expected = _normalize_text(capability)
    matches = [
        candidate
        for candidate in candidates
        if _normalize_text(str(candidate.get("capability") or "")) == expected
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _find_coverage_by_contract_refs(
    capability: str,
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    expected_refs = set(_extract_contract_refs_from_capability(capability))

    if not expected_refs:
        return None

    matches: List[Dict[str, Any]] = []

    for candidate in candidates:
        candidate_refs = {
            str(ref).strip()
            for ref in candidate.get("contract_refs", [])
            if str(ref).strip()
        }
        if expected_refs & candidate_refs:
            matches.append(candidate)

    if len(matches) == 1:
        return matches[0]

    return None


def _coverage_valid_reference_count(
    coverage: Dict[str, Any],
    contract_index: Dict[str, Any],
    action_index: Dict[str, Any],
) -> int:
    count = 0
    for ref in coverage.get("contract_refs", []):
        if ref in contract_index:
            count += 1
    for ref in coverage.get("action_refs", []):
        if ref in action_index:
            count += 1
    return count


def _find_unique_structural_coverage(
    capability: str,
    candidates: List[Dict[str, Any]],
    contract_index: Dict[str, Any],
    action_index: Dict[str, Any],
) -> Dict[str, Any] | None:
    if _extract_contract_refs_from_capability(capability):
        return None

    valid_candidates = [
        candidate
        for candidate in candidates
        if _coverage_valid_reference_count(candidate, contract_index, action_index) > 0
    ]

    if len(valid_candidates) == 1:
        return valid_candidates[0]

    return None


def _build_direct_contract_coverage(
    *,
    capability_id: str,
    capability: str,
    contract_refs: List[str],
    contract_index: Dict[str, Any],
) -> Dict[str, Any] | None:
    if not contract_refs:
        return None

    valid_refs = [
        ref
        for ref in _dedupe_str_list(contract_refs)
        if ref in contract_index
    ]

    if len(valid_refs) != len(_dedupe_str_list(contract_refs)):
        return None

    return {
        "capability_id": capability_id,
        "capability": capability,
        "contract_refs": valid_refs,
        "action_refs": [],
        "integration_refs": [],
        "status": "covered",
        "source": "derived",
        "evidence": ", ".join(valid_refs),
        "assumption": "",
    }


def _reconcile_single_coverage(
    coverage: Dict[str, Any],
    *,
    contract_index: Dict[str, Dict[str, Any]],
    action_index: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]],
) -> Dict[str, Any]:
    contract_refs = _dedupe_str_list(_ensure_list_of_str(coverage.get("contract_refs")))
    action_refs = _dedupe_str_list(_ensure_list_of_str(coverage.get("action_refs")))
    integration_refs = _dedupe_str_list(_ensure_list_of_str(coverage.get("integration_refs")))

    for action_ref in action_refs:
        indexed = action_index.get(action_ref)
        if indexed is None:
            continue
        action = indexed[1]
        if (
            str(action.get("kind") or "").strip().lower() == "external_call"
            and isinstance(action.get("integration_ref"), str)
            and action.get("integration_ref", "").strip()
            and action["integration_ref"] not in integration_refs
        ):
            integration_refs.append(action["integration_ref"])

    for contract_ref in contract_refs:
        contract = contract_index.get(contract_ref)
        if not contract:
            continue

        matching_actions: List[Dict[str, Any]] = []
        for action in contract.get("actions") or []:
            if not isinstance(action, dict):
                continue
            if str(action.get("kind") or "").strip().lower() != "external_call":
                continue
            action_integration = _coalesce_str(action.get("integration_ref"), "")
            if integration_refs and action_integration not in integration_refs:
                continue
            matching_actions.append(action)

        if len(matching_actions) == 1:
            action = matching_actions[0]
            action_ref = f"{contract_ref}#{str(action.get('id') or '').strip()}"
            if action_ref not in action_refs:
                action_refs.append(action_ref)

    return {
        "capability_id": _coalesce_str(coverage.get("capability_id"), ""),
        "capability": _coalesce_str(coverage.get("capability"), ""),
        "contract_refs": contract_refs,
        "action_refs": action_refs,
        "integration_refs": integration_refs,
        "status": str(coverage.get("status") or "uncovered").strip() or "uncovered",
        "source": str(coverage.get("source") or "unknown").strip() or "unknown",
        "evidence": _coalesce_str(coverage.get("evidence"), ""),
        "assumption": _coalesce_str(coverage.get("assumption"), ""),
    }


def _dedupe_capability_coverage(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for index, item in enumerate(items):
        capability_id = _coalesce_str(item.get("capability_id"), "")
        capability = _coalesce_str(item.get("capability"), "")
        key = capability_id.lower() or f"text::{_normalize_text(capability)}::{index}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _append_open_question(data: Dict[str, Any], message: str) -> None:
    open_questions = list(data.get("open_questions") or [])
    open_questions.append(message)
    data["open_questions"] = _dedupe_str_list(open_questions)


def _reconcile_capability_coverage_inplace(data: Dict[str, Any]) -> None:
    capabilities = _ensure_list_of_str(data.get("funcionalidades_clave"))
    existing = _normalize_capability_coverage(data.get("capability_coverage"))
    original_coverage_was_present = bool(existing)

    if capabilities and not existing:
        _append_open_question(
            data,
            "Existen capacidades funcionales, pero el normalizador no produjo capability_coverage.",
        )
        data["capability_coverage"] = []
        return

    capability_entries = _assign_capability_ids(capabilities)
    unmatched = list(existing)
    reconciled: List[Dict[str, Any]] = []
    unresolved_capabilities: List[str] = []
    links: List[Dict[str, Any]] = []

    contract_index = _build_contract_index(data)
    action_index = _build_action_index(data)

    for capability_entry in capability_entries:
        capability_id = capability_entry["id"]
        capability_text = capability_entry["description"]
        current = _find_exact_text_coverage(
            capability_text,
            unmatched,
        )
        match_type = None

        if current is not None:
            match_type = "exact_text"

        if current is None:
            current = _find_coverage_by_contract_refs(
                capability_text,
                unmatched,
            )
            if current is not None:
                match_type = "contract_ref"

        explicit_contract_refs = _extract_contract_refs_from_capability(
            capability_text
        )

        if current is None and explicit_contract_refs:
            direct_coverage = _build_direct_contract_coverage(
                capability_id=capability_id,
                capability=capability_text,
                contract_refs=explicit_contract_refs,
                contract_index=contract_index,
            )
            if direct_coverage is not None:
                reconciled.append(direct_coverage)
                links.append(
                    {
                        "capability": capability_text,
                        "capability_id": capability_id,
                        "coverage_capability": "",
                        "match_type": "direct_contract",
                        "contract_refs": explicit_contract_refs,
                    }
                )
                continue

        if current is None and not explicit_contract_refs:
            current = _find_unique_structural_coverage(
                capability=capability_text,
                candidates=unmatched,
                contract_index=contract_index,
                action_index=action_index,
            )
            if current is not None:
                match_type = "unique_structural"

        if current is None:
            unresolved_capabilities.append(capability_text)
            links.append(
                {
                    "capability": capability_text,
                    "capability_id": capability_id,
                    "coverage_capability": "",
                    "match_type": "unresolved",
                    "contract_refs": explicit_contract_refs,
                }
            )
            continue

        if current in unmatched:
            unmatched.remove(current)

        normalized = _reconcile_single_coverage(
            current,
            contract_index=contract_index,
            action_index=action_index,
        )
        normalized["capability_id"] = capability_id
        normalized["capability"] = capability_text
        reconciled.append(normalized)
        links.append(
            {
                "capability": capability_text,
                "capability_id": capability_id,
                "coverage_capability": _coalesce_str(current.get("capability"), ""),
                "match_type": match_type,
            }
        )

    for coverage in unmatched:
        normalized = _reconcile_single_coverage(
            coverage,
            contract_index=contract_index,
            action_index=action_index,
        )
        reconciled.append(normalized)
        _append_open_question(
            data,
            "Capability coverage producida pero no vinculada a funcionalidades_clave: "
            f"{coverage.get('capability', '')}",
        )

    if unresolved_capabilities:
        _append_open_question(
            data,
            "No se pudo vincular inequívocamente capability_coverage con funcionalidades_clave.",
        )

    for capability in unresolved_capabilities:
        _append_open_question(
            data,
            f"Capacidad obligatoria sin cobertura vinculada: {capability}",
        )

    data["capability_coverage"] = _dedupe_capability_coverage(reconciled)

    if original_coverage_was_present and not data["capability_coverage"]:
        _append_open_question(
            data,
            "La reconciliación eliminó todas las coberturas producidas por el normalizador.",
        )

    _persist_debug_artifact_json(
        "capability_coverage_before_reconciliation",
        {"before": existing},
    )
    data["_debug"] = {
        "links": links,
        "unresolved_capabilities": list(unresolved_capabilities),
        "unmatched_coverages": [
            {
                "capability_id": _coalesce_str(item.get("capability_id"), ""),
                "capability": _coalesce_str(item.get("capability"), ""),
            }
            for item in unmatched
        ],
    }

    _persist_debug_artifact_json(
        "capability_coverage_after_reconciliation",
        {
            "before": existing,
            "after": data["capability_coverage"],
            "links": links,
            "unresolved_capabilities": unresolved_capabilities,
            "unmatched_coverages": [
                {
                    "capability_id": _coalesce_str(item.get("capability_id"), ""),
                    "capability": _coalesce_str(item.get("capability"), ""),
                }
                for item in unmatched
            ],
        },
    )


def _build_contract_index(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for key in ("contratos_api_explicitos", "contratos_api_propuestos"):
        for contract in data.get(key) or []:
            if not isinstance(contract, dict):
                continue
            method = str(contract.get("method") or "").upper().strip()
            path = _normalize_path_key(str(contract.get("path") or ""))
            if not method or not path:
                continue
            index[f"{method} {path}"] = contract
    return index


def _build_action_index(data: Dict[str, Any]) -> Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]]:
    index: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
    for contract_ref, contract in _build_contract_index(data).items():
        for action in contract.get("actions") or []:
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("id") or "").strip()
            if not action_id:
                continue
            index[f"{contract_ref}#{action_id}"] = (contract, action)
    return index


def _action_ref_exists(contract_index: Dict[str, Dict[str, Any]], action_ref: str) -> bool:
    if "#" not in action_ref:
        return False
    contract_ref, action_id = action_ref.split("#", 1)
    contract = contract_index.get(contract_ref.strip())
    if not contract:
        return False
    for action in contract.get("actions") or []:
        if isinstance(action, dict) and str(action.get("id") or "").strip() == action_id.strip():
            return True
    return False


def _build_capability_coverage_report(data: Dict[str, Any]) -> Dict[str, Any]:
    coverage = _normalize_capability_coverage(data.get("capability_coverage"))
    capabilities = _ensure_list_of_str(data.get("funcionalidades_clave"))
    missing_coverage = bool(capabilities and not coverage)
    return {
        "capabilities": capabilities,
        "missing_capability_coverage": missing_coverage,
        "missing_capability_coverage_error": (
            "Existen capacidades funcionales, pero el normalizador no produjo capability_coverage."
            if missing_coverage
            else ""
        ),
        "covered": [item["capability"] for item in coverage if item.get("status") == "covered"],
        "partially_covered": [
            item["capability"] for item in coverage if item.get("status") == "partially_covered"
        ],
        "uncovered": [item["capability"] for item in coverage if item.get("status") == "uncovered"],
        "contracts": {
            "explicit": [
                f"{str(c.get('method') or '').upper().strip()} {_normalize_path_key(str(c.get('path') or ''))}"
                for c in data.get("contratos_api_explicitos") or []
                if isinstance(c, dict)
            ],
            "proposed": [
                f"{str(c.get('method') or '').upper().strip()} {_normalize_path_key(str(c.get('path') or ''))}"
                for c in data.get("contratos_api_propuestos") or []
                if isinstance(c, dict)
            ],
        },
        "integrations": [
            str(item.get("id") or "").strip()
            for item in data.get("integrations") or []
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ],
    }


def _ensure_list_of_str(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for x in v:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _dedupe_str_list(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    out: List[str] = []
    seen = set()
    for x in items:
        if not isinstance(x, str):
            continue
        s = x.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _template_evidence_lines(plantilla: PlantillaUsuario) -> List[str]:
    out: List[str] = []
    if plantilla.problema:
        out.append(f"Problema: {plantilla.problema}")
    if plantilla.usuarios:
        out.append(f"Usuarios: {plantilla.usuarios}")
    if plantilla.funcionalidades:
        out.append(f"Funcionalidades: {plantilla.funcionalidades}")
    if plantilla.limites:
        out.append(f"Límites: {plantilla.limites}")
    if plantilla.tecnologias:
        out.append(f"Tecnologías declaradas: {plantilla.tecnologias}")
    return out


def _normalize_source(value: Any) -> str:
    allowed = {"explicit", "inferred", "default", "unknown"}
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else "unknown"


def _normalize_implementation_level(value: Any) -> str:
    allowed = {
        "fully_local",
        "integration_skeleton",
        "mocked",
        "documentation_only",
    }
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else "integration_skeleton"


def _normalize_integrations(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    allowed_kinds = {
        "external_api",
        "database",
        "queue",
        "cache",
        "object_storage",
        "email",
        "auth",
        "observability",
        "runtime",
        "other",
    }

    out: List[Dict[str, Any]] = []
    seen = set()

    for item in raw:
        if not isinstance(item, dict):
            continue

        integration_id = item.get("id") if isinstance(item.get("id"), str) else ""
        name = item.get("name") if isinstance(item.get("name"), str) else ""
        integration_id = integration_id.strip()
        name = name.strip()
        if not integration_id or not name:
            continue

        kind_raw = item.get("kind") if isinstance(item.get("kind"), str) else ""
        kind = kind_raw.strip().lower()
        if kind not in allowed_kinds:
            kind = "other"

        role = item.get("role") if isinstance(item.get("role"), str) else ""
        source = _normalize_source(item.get("source"))
        evidence = item.get("evidence") if isinstance(item.get("evidence"), str) else ""
        assumption = item.get("assumption") if isinstance(item.get("assumption"), str) else ""
        required = bool(item.get("required")) if "required" in item else False

        auth_raw = item.get("authentication") if isinstance(item.get("authentication"), dict) else {}
        auth_mechanism = (
            auth_raw.get("mechanism").strip()
            if isinstance(auth_raw.get("mechanism"), str) and auth_raw.get("mechanism").strip()
            else ""
        )
        auth_source = _normalize_source(auth_raw.get("source"))
        auth_evidence = auth_raw.get("evidence") if isinstance(auth_raw.get("evidence"), str) else ""
        auth_assumption = (
            auth_raw.get("assumption") if isinstance(auth_raw.get("assumption"), str) else ""
        )

        source, evidence, assumption = _sanitize_source_evidence_assumption(
            source=source,
            evidence=evidence,
            assumption=assumption,
            missing_evidence_assumption="Se degradó source=explicit a inferred por falta de evidencia literal suficiente.",
        )
        auth_source, auth_evidence, auth_assumption = _sanitize_source_evidence_assumption(
            source=auth_source,
            evidence=auth_evidence,
            assumption=auth_assumption,
            missing_evidence_assumption="Se degradó authentication.source=explicit a inferred por falta de evidencia literal suficiente.",
        )

        normalized = {
            "id": integration_id,
            "name": name,
            "kind": kind,
            "role": role.strip(),
            "required": required,
            "implementation_level": _normalize_implementation_level(item.get("implementation_level")),
            "authentication": {
                "mechanism": auth_mechanism,
                "credential_source": _normalize_credential_source(auth_raw.get("credential_source")),
                "allows_embedded_secret": bool(auth_raw.get("allows_embedded_secret"))
                if "allows_embedded_secret" in auth_raw
                else False,
                "allows_static_credential_file": bool(auth_raw.get("allows_static_credential_file"))
                if "allows_static_credential_file" in auth_raw
                else True,
                "source": auth_source,
                "evidence": auth_evidence.strip(),
                "assumption": auth_assumption.strip(),
            },
            "technology_refs": _dedupe_str_list(_ensure_list_of_str(item.get("technology_refs"))),
            "configuration_refs": _dedupe_str_list(_ensure_list_of_str(item.get("configuration_refs"))),
            "source": source,
            "evidence": evidence.strip(),
            "assumption": assumption.strip(),
        }

        dedupe_key = normalized["id"].lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(normalized)

    return out


def _normalize_configuration(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    out: List[Dict[str, Any]] = []
    seen = set()

    for item in raw:
        if not isinstance(item, dict):
            continue

        key = item.get("key") if isinstance(item.get("key"), str) else ""
        purpose = item.get("purpose") if isinstance(item.get("purpose"), str) else ""
        key = key.strip()
        purpose = purpose.strip()
        if not key or not purpose:
            continue

        source = _normalize_source(item.get("source"))
        evidence = item.get("evidence") if isinstance(item.get("evidence"), str) else ""
        assumption = item.get("assumption") if isinstance(item.get("assumption"), str) else ""
        source, evidence, assumption = _sanitize_source_evidence_assumption(
            source=source,
            evidence=evidence,
            assumption=assumption,
            missing_evidence_assumption="Se degradó source=explicit a inferred por falta de evidencia literal suficiente.",
        )

        normalized = {
            "key": key,
            "purpose": purpose,
            "required": bool(item.get("required")) if "required" in item else False,
            "secret": bool(item.get("secret")) if "secret" in item else False,
            "source": source,
            "evidence": evidence.strip(),
            "assumption": assumption.strip(),
            "delivery": _normalize_configuration_delivery(item.get("delivery")),
        }

        dedupe_key = normalized["key"].lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(normalized)

    return out


def _normalize_contract_actions(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    allowed_kinds = {
        "internal_processing",
        "persistence",
        "external_call",
        "validation",
        "transformation",
        "notification",
        "other",
    }

    out: List[Dict[str, Any]] = []
    seen = set()

    for item in raw:
        if not isinstance(item, dict):
            continue

        action_id = item.get("id") if isinstance(item.get("id"), str) else ""
        description = item.get("description") if isinstance(item.get("description"), str) else ""
        action_id = action_id.strip()
        description = description.strip()
        if not action_id or not description:
            continue

        kind_raw = item.get("kind") if isinstance(item.get("kind"), str) else ""
        kind = kind_raw.strip().lower()
        if kind not in allowed_kinds:
            kind = "other"

        integration_ref = item.get("integration_ref")
        if not (isinstance(integration_ref, str) and integration_ref.strip()):
            integration_ref = None
        else:
            integration_ref = integration_ref.strip()

        source = _normalize_source(item.get("source"))
        evidence = item.get("evidence") if isinstance(item.get("evidence"), str) else ""
        assumption = item.get("assumption") if isinstance(item.get("assumption"), str) else ""
        source, evidence, assumption = _sanitize_source_evidence_assumption(
            source=source,
            evidence=evidence,
            assumption=assumption,
            missing_evidence_assumption="Se degradó source=explicit a inferred por falta de evidencia literal suficiente.",
        )

        normalized = {
            "id": action_id,
            "kind": kind,
            "description": description,
            "required": bool(item.get("required")) if "required" in item else False,
            "integration_ref": integration_ref,
            "source": source,
            "evidence": evidence.strip(),
            "assumption": assumption.strip(),
        }

        dedupe_key = normalized["id"].lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(normalized)

    return out


def _normalize_contract_errors(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    out: List[Dict[str, Any]] = []
    seen = set()

    for item in raw:
        if not isinstance(item, dict):
            continue

        code = item.get("code") if isinstance(item.get("code"), str) else ""
        description = item.get("description") if isinstance(item.get("description"), str) else ""
        code = code.strip()
        description = description.strip()
        if not code or not description:
            continue

        status_code = item.get("status_code")
        if isinstance(status_code, bool):
            status_code = None
        elif isinstance(status_code, int):
            pass
        elif isinstance(status_code, float) and status_code.is_integer():
            status_code = int(status_code)
        else:
            status_code = None

        source = _normalize_source(item.get("source"))
        evidence = item.get("evidence") if isinstance(item.get("evidence"), str) else ""
        assumption = item.get("assumption") if isinstance(item.get("assumption"), str) else ""
        source, evidence, assumption = _sanitize_source_evidence_assumption(
            source=source,
            evidence=evidence,
            assumption=assumption,
            missing_evidence_assumption="Se degradó source=explicit a inferred por falta de evidencia literal suficiente.",
        )

        normalized = {
            "status_code": status_code,
            "code": code,
            "description": description,
            "required": bool(item.get("required")) if "required" in item else False,
            "source": source,
            "evidence": evidence.strip(),
            "assumption": assumption.strip(),
        }

        dedupe_key = (normalized["status_code"], normalized["code"].lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(normalized)

    return out


def _normalize_contract_shape(contract: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(contract)

    request = out.get("request") if isinstance(out.get("request"), dict) else {}
    response = out.get("response") if isinstance(out.get("response"), dict) else {}

    out["method"] = str(out.get("method") or "").upper().strip()
    out["path"] = _normalize_path_key(str(out.get("path") or ""))
    out["request"] = dict(request)
    out["response"] = dict(response)
    out["actions"] = _normalize_contract_actions(out.get("actions"))
    out["errors"] = _normalize_contract_errors(out.get("errors"))
    out["integration_refs"] = _dedupe_str_list(_ensure_list_of_str(out.get("integration_refs")))

    return out


def _sanitize_source_evidence_assumption(
    *,
    source: str,
    evidence: str,
    assumption: str,
    missing_evidence_assumption: str,
) -> Tuple[str, str, str]:
    source_norm = _normalize_source(source)
    evidence_norm = evidence.strip() if isinstance(evidence, str) else ""
    assumption_norm = assumption.strip() if isinstance(assumption, str) else ""

    if source_norm == "explicit" and not evidence_norm:
        source_norm = "inferred"
        if not assumption_norm:
            assumption_norm = missing_evidence_assumption

    return source_norm, evidence_norm, assumption_norm


def _normalize_persistence(p: Any, assumptions_sink: List[str] | None = None) -> Dict[str, Any]:
    sink = assumptions_sink if isinstance(assumptions_sink, list) else []

    if not isinstance(p, dict):
        p = {}

    required = bool(p.get("required")) if "required" in p else False
    durable_state = bool(p.get("durable_state")) if "durable_state" in p else False

    kind = p.get("kind")
    if kind is None:
        kind_norm = None
    elif isinstance(kind, str) and kind.strip() in (
        "relational",
        "document",
        "key_value",
        "object_storage",
        "event_log",
        "unknown",
    ):
        kind_norm = kind.strip()
    else:
        kind_norm = "unknown" if required else None
        if required and sink is not None:
            sink.append("persistence.kind no era válido/no permitido; se degradó a 'unknown'.")

    business_entities = _ensure_list_of_str(p.get("business_entities"))
    evidence = _ensure_list_of_str(p.get("evidence"))
    uncertainty = p.get("uncertainty") if isinstance(p.get("uncertainty"), str) else ""
    uncertainty = uncertainty.strip()

    if durable_state and not evidence:
        if sink is not None:
            sink.append("durable_state_without_evidence")
            sink.append(
                "durable_state_without_evidence: Se ignoró persistence.durable_state=True por falta de evidence."
            )
        if not uncertainty:
            uncertainty = "durable_state_without_evidence"
        return {
            "required": False,
            "kind": None,
            "durable_state": False,
            "business_entities": [],
            "evidence": [],
            "uncertainty": uncertainty,
        }

    if not required:
        return {
            "required": False,
            "kind": None,
            "durable_state": False,
            "business_entities": [],
            "evidence": [],
            "uncertainty": uncertainty,
        }

    if not evidence and sink is not None:
        sink.append(
            "Persistencia marcada como requerida pero falta evidence; revisar/confirmar necesidad de estado durable."
        )
        if not uncertainty:
            uncertainty = "required_true_without_evidence"

    return {
        "required": True,
        "kind": kind_norm if kind_norm is not None else "unknown",
        "durable_state": bool(durable_state),
        "business_entities": business_entities,
        "evidence": evidence,
        "uncertainty": uncertainty,
    }


def _normalize_credential_source(value: Any) -> str:
    allowed = {"runtime", "file", "environment", "request", "unknown"}
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else "unknown"


def _normalize_configuration_delivery(value: Any) -> str:
    allowed = {"env", "file", "argument", "runtime", "unknown"}
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else "env"


def _normalize_technology_signals(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    allowed_categories = {
        "framework",
        "persistence",
        "cache",
        "queue",
        "object_storage",
        "search",
        "external_api",
        "auth",
        "observability",
        "runtime",
        "library",
        "unknown",
    }
    allowed_confidence = {"explicit", "inferred", "unknown"}

    out: List[Dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        name = it.get("name") if isinstance(it.get("name"), str) else ""
        evidence = it.get("evidence") if isinstance(it.get("evidence"), str) else ""
        if not name.strip():
            continue
        category = it.get("category") if isinstance(it.get("category"), str) else "unknown"
        category = category.strip() if category.strip() in allowed_categories else "unknown"

        role = it.get("role") if isinstance(it.get("role"), str) else ""
        role = role.strip()

        confidence = it.get("confidence") if isinstance(it.get("confidence"), str) else "unknown"
        confidence = confidence.strip() if confidence.strip() in allowed_confidence else "unknown"

        packages = _dedupe_str_list(_ensure_list_of_str(it.get("packages")))
        legacy_package = str(it.get("package") or "").strip()
        if legacy_package and legacy_package not in packages:
            packages.append(legacy_package)

        out.append(
            {
                "id": str(it.get("id") or "").strip(),
                "name": name.strip(),
                "category": category,
                "packages": packages,
                "import_roots": _dedupe_str_list(_ensure_list_of_str(it.get("import_roots"))),
                "role": role,
                "evidence": evidence.strip(),
                "confidence": confidence,
            }
        )

    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in out:
        k = (it["name"].lower(), it["category"].lower(), it["role"].lower())
        if k in seen:
            continue
        seen.add(k)
        deduped.append(it)

    return deduped


def _normalize_domain_entities(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    allowed_confidence = {"explicit", "inferred", "unknown"}

    out: List[Dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue

        evidence = it.get("evidence") if isinstance(it.get("evidence"), str) else ""
        if not evidence.strip():
            continue

        name = it.get("name") if isinstance(it.get("name"), str) else ""
        singular = it.get("singular") if isinstance(it.get("singular"), str) else ""
        plural = it.get("plural") if isinstance(it.get("plural"), str) else ""
        confidence = it.get("confidence") if isinstance(it.get("confidence"), str) else "unknown"
        confidence = confidence.strip() if confidence.strip() in allowed_confidence else "unknown"

        slug_source = (plural or name or singular).strip()
        slug = _slugify_path_segment(slug_source)

        out.append(
            {
                "name": name.strip() or slug_source,
                "singular": singular.strip(),
                "plural": plural.strip(),
                "slug": slug,
                "evidence": evidence.strip(),
                "confidence": confidence,
            }
        )

    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in out:
        k = (it.get("slug") or it.get("name") or "").lower()
        if not k or k in seen:
            continue
        seen.add(k)
        deduped.append(it)

    return deduped


def _normalize_operation_groups(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    allowed_confidence = {"explicit", "inferred", "unknown"}

    out: List[Dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        t = it.get("type") if isinstance(it.get("type"), str) else ""
        entity = it.get("entity") if isinstance(it.get("entity"), str) else ""
        evidence = it.get("evidence") if isinstance(it.get("evidence"), str) else ""
        if not t.strip() or not entity.strip() or not evidence.strip():
            continue

        confidence = it.get("confidence") if isinstance(it.get("confidence"), str) else "unknown"
        confidence = confidence.strip() if confidence.strip() in allowed_confidence else "unknown"

        out.append(
            {
                "type": t.strip().lower(),
                "entity": entity.strip(),
                "evidence": evidence.strip(),
                "confidence": confidence,
            }
        )

    return out


def _slugify_path_segment(value: str) -> str:
    v = (value or "").strip().lower()
    if not v:
        return ""
    out: List[str] = []
    prev_dash = False
    for ch in v:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        else:
            if not prev_dash:
                out.append("-")
                prev_dash = True
    return "".join(out).strip("-")


def _normalize_state_requirements(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    durable = bool(raw.get("durable")) if "durable" in raw else False
    entities = _ensure_list_of_str(raw.get("entities"))
    evidence = _ensure_list_of_str(raw.get("evidence"))
    return {"durable": bool(durable), "entities": entities, "evidence": evidence}


def _reconcile_persistence_and_state_requirements(data: Dict[str, Any]) -> None:
    if not isinstance(data, dict):
        return

    p = data.get("persistence") if isinstance(data.get("persistence"), dict) else {}
    s = data.get("state_requirements") if isinstance(data.get("state_requirements"), dict) else {}

    assumptions = data.get("assumptions")
    if not isinstance(assumptions, list):
        assumptions = []
        data["assumptions"] = assumptions

    p_required = bool(p.get("required")) if "required" in p else False
    p_evidence = _ensure_list_of_str(p.get("evidence"))

    s_durable = bool(s.get("durable")) if "durable" in s else False
    s_evidence = _ensure_list_of_str(s.get("evidence"))

    if p_required and not s_durable:
        if p_evidence:
            s["durable"] = True
            if not s.get("entities"):
                s["entities"] = _ensure_list_of_str(p.get("business_entities"))
            if not s_evidence:
                s["evidence"] = list(p_evidence)
        else:
            assumptions.append(
                "Contradicción: persistence.required=True pero sin evidence; se degradó a required=False."
            )
            data["persistence"] = {
                "required": False,
                "kind": None,
                "durable_state": False,
                "business_entities": [],
                "evidence": [],
                "uncertainty": p.get("uncertainty") or "contradiction_without_evidence",
            }

    p = data.get("persistence") if isinstance(data.get("persistence"), dict) else p
    p_required = bool(p.get("required")) if "required" in p else False

    if s_durable and not p_required:
        if s_evidence:
            data["persistence"] = {
                "required": True,
                "kind": "unknown",
                "durable_state": True,
                "business_entities": _ensure_list_of_str(s.get("entities")),
                "evidence": list(s_evidence),
                "uncertainty": p.get("uncertainty") if isinstance(p.get("uncertainty"), str) else "",
            }
        else:
            assumptions.append(
                "state_requirements.durable=True sin evidence; se mantuvo persistence.required=False (conservador)."
            )
            s["durable"] = False
            s["entities"] = []
            s["evidence"] = []

    p = data.get("persistence") if isinstance(data.get("persistence"), dict) else p
    s = data.get("state_requirements") if isinstance(data.get("state_requirements"), dict) else s
    if bool(p.get("required")) and bool(s.get("durable")):
        p["evidence"] = _dedupe_str_list(_ensure_list_of_str(p.get("evidence")))
        p["business_entities"] = _dedupe_str_list(_ensure_list_of_str(p.get("business_entities")))
        s["evidence"] = _dedupe_str_list(_ensure_list_of_str(s.get("evidence")))
        s["entities"] = _dedupe_str_list(_ensure_list_of_str(s.get("entities")))

    data["persistence"] = p
    data["state_requirements"] = s


def _persist_debug_artifact_text(prefix: str, content: str) -> None:
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (debug_dir / f"{prefix}_{ts}.txt").write_text(
            content if isinstance(content, str) else str(content),
            encoding="utf-8",
        )
    except Exception:
        pass


def _persist_debug_artifact_json(prefix: str, payload: Any) -> None:
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (debug_dir / f"{prefix}_{ts}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _persist_debug_context(plantilla: PlantillaUsuario, data: Dict[str, Any]) -> None:
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (debug_dir / f"contexto_normalizado_{ts}.json").write_text(
            json.dumps(
                {
                    "timestamp": ts,
                    "plantilla": {
                        "nombre": plantilla.nombre,
                        "problema": plantilla.problema,
                        "usuarios": plantilla.usuarios,
                        "funcionalidades": plantilla.funcionalidades,
                        "limites": plantilla.limites,
                        "tecnologias": plantilla.tecnologias,
                    },
                    "contexto_normalizado": data,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _stable_snake_case_id(value: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return base or "external_system"


def _normalize_path_key(path: str) -> str:
    p = str(path or "").strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/") if p != "/" else p


def _coalesce_str(*vals: Any) -> str:
    for value in vals:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
