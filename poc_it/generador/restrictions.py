from __future__ import annotations

import json
from enum import Enum
from typing import Any, List

from poc_it.generador.json_utils import extraer_json_tolerante
from poc_it.infraestructura.llm_client import chat_completion_json


class ConstraintEnforcement(str, Enum):
    CODE = "code"
    RUNTIME = "runtime"
    EXTERNAL_PRECONDITION = "external_precondition"
    DOCUMENTATION = "documentation"


def sanitizar_restrictions(restrictions: List[dict]) -> List[dict]:
    """
    Limpieza defensiva y GENÉRICA de restrictions (enforcement-ready).

    Objetivo:
    - Mantener restricciones útiles (evitar alucinaciones claras).
    - Reducir falsos positivos que bloquean PoCs realistas.

    Convenciones:
    - Cada restriction puede incluir opcionalmente:
      - severity: "BLOCK" | "WARN"
      - kind: "SECURITY" | "STRUCTURAL" | "CONTRACT" | "QUALITY"
    - Si no viene severity, la inferimos por TIPO (kind) y por heurísticas de riesgo
      (sin mirar palabras clave específicas de una PoC concreta).
    """
    if not isinstance(restrictions, list):
        return []

    def _norm_patterns(applies_to_val: Any) -> List[str]:
        if not isinstance(applies_to_val, list):
            return []
        return [
            str(x).replace("\\", "/").strip() for x in applies_to_val if str(x).strip()
        ]

    def _is_overbroad(applies_to_val: Any) -> bool:
        """
        Overbroad = aplica a "todo" o a scopes tan amplios que hacen `must_contain_any` frágil.
        Ejemplos típicos:
        - ["*"], ["*.py"]
        - ["app/*"], ["app/"], ["app/*.py"]
        - cualquier lista que incluya "*" o "*.py"
        """
        norm = _norm_patterns(applies_to_val)
        if not norm:
            return True
        if "*" in norm or "*.py" in norm:
            return True
        if any(p in ("app/*", "app/", "app/*.py") for p in norm):
            return True
        return False

    def _infer_kind(rr: dict) -> str:
        """
        Clasificación de tipo (agnóstica):
        - SECURITY: evitar secretos/credenciales embebidas
        - STRUCTURAL: invariantes del generador (paths/imports/routers), baja tolerancia al incumplimiento
        - CONTRACT: invariantes del contrato (request/response/endpoints) pero suelen ser verificables por otras vías
        - QUALITY: estilo/calidad/mantenibilidad (no deben bloquear generación)
        """
        kind = str(rr.get("kind") or "").strip().upper()
        if kind in ("SECURITY", "STRUCTURAL", "CONTRACT", "QUALITY"):
            return kind

        must_not = [
            str(x).lower()
            for x in (rr.get("must_not_contain") or [])
            if str(x).strip()
        ]
        must_any = [
            str(x).lower()
            for x in (rr.get("must_contain_any") or [])
            if str(x).strip()
        ]

        # Heurística genérica de secretos (alto riesgo real)
        high_risk = ("password", "private_key", "api_key", "apikey", "secret", "token=")
        if any(any(t in p for t in high_risk) for p in (must_not + must_any)):
            return "SECURITY"

        # must_contain_any suele ser "contractual/guía", no estructural
        if must_any:
            return "CONTRACT"

        # default conservador: QUALITY (no bloquear)
        return "QUALITY"

    def _inferir_severidad(rr: dict) -> str:
        """
        Política por tipo:
        - SECURITY -> BLOCK (evitar credenciales/secretos en código)
        - STRUCTURAL -> BLOCK (si se usan para invariantes internas del generador)
        - CONTRACT -> WARN (preferimos no bloquear: hay otras validaciones deterministas)
        - QUALITY -> WARN
        """
        kind = _infer_kind(rr)
        if kind in ("SECURITY", "STRUCTURAL"):
            return "BLOCK"
        return "WARN"

    cleaned: List[dict] = []
    for r in restrictions:
        if not isinstance(r, dict):
            continue

        rr = dict(r)

        # Normalización defensiva
        applies_to = rr.get("applies_to")
        must_any = rr.get("must_contain_any") or []
        must_not = rr.get("must_not_contain") or []

        if not isinstance(must_any, list):
            must_any = [str(must_any)]
        if not isinstance(must_not, list):
            must_not = [str(must_not)]
        rr["must_contain_any"] = must_any
        rr["must_not_contain"] = must_not

        rr["kind"] = _infer_kind(rr)

        enforcement = str(rr.get("enforcement") or "").strip().lower()
        if enforcement not in {
            ConstraintEnforcement.CODE.value,
            ConstraintEnforcement.RUNTIME.value,
            ConstraintEnforcement.EXTERNAL_PRECONDITION.value,
            ConstraintEnforcement.DOCUMENTATION.value,
        }:
            enforcement = ConstraintEnforcement.CODE.value
        rr["enforcement"] = enforcement

        # Severidad: si no viene, inferir por tipo (kind)
        severity = str(rr.get("severity") or "").strip().upper()
        if severity not in ("BLOCK", "WARN"):
            severity = _inferir_severidad(rr)
        if enforcement != ConstraintEnforcement.CODE.value:
            severity = "WARN"
        rr["severity"] = severity

        def _looks_api_specific_must_any(patterns: List[str]) -> bool:
            """
            must_contain_any demasiado específico a una API/implementación concreta.
            Ejemplo: exigir exactamente os.getenv('DATABASE_URL') cuando también sería válido
            usar Settings, os.environ.get, etc.
            """
            joined = " ".join(str(x) for x in patterns)
            return (
                "os.getenv(" in joined
                or "os.environ[" in joined
                or "os.environ.get(" in joined
            )

        # must_contain_any es frágil si aplica a demasiado scope:
        # - lo anulamos
        # - y degradamos severidad a WARN
        if must_any and _is_overbroad(applies_to):
            rr["must_contain_any"] = []
            rr["severity"] = "WARN"
            rr["kind"] = "QUALITY"

        # aunque el scope sea específico, si must_contain_any exige una API concreta, no bloqueamos
        if must_any and _looks_api_specific_must_any(must_any):
            rr["severity"] = "WARN"
            if rr.get("kind") == "STRUCTURAL":
                rr["kind"] = "CONTRACT"

        cleaned.append(rr)

    return cleaned


def compilar_restricciones(
    spec: dict,
    contexto_normalizado: dict | None,
    intentos: int = 2,
) -> List[dict]:
    """
    Compila restricciones en lenguaje natural (contexto_normalizado.restricciones_tecnicas)
    a reglas ejecutables (spec.restrictions).

    Objetivo:
    - Mecanismo genérico para que el sistema NO ignore restricciones del usuario.
    - Evita hardcodear tecnologías: la traducción a patrones la hace un LLM pequeño (JSON).
    - Salida: lista de dicts con keys: id, applies_to?, must_not_contain?, must_contain_any?
    """
    restricciones = []
    if isinstance(spec.get("restrictions"), list):
        restricciones = [
            r for r in spec.get("restrictions", []) if isinstance(r, dict)
        ]

    # Si ya hay restricciones ejecutables, las respetamos y no gastamos tokens.
    # PERO: aplicamos una sanitización genérica para evitar falsos positivos masivos.
    if restricciones:
        return sanitizar_restrictions(restricciones)

    restricciones_tecnicas = []
    if isinstance(contexto_normalizado, dict):
        restricciones_tecnicas = contexto_normalizado.get("restricciones_tecnicas") or []

    # Si no hay restricciones en texto, no generamos nada.
    if not restricciones_tecnicas:
        return []

    prompt = f"""
TAREA
Convierte las RESTRICCIONES del usuario (texto) en reglas ejecutables para validar código.

ENTRADA
- Restricciones (texto):
{json.dumps(restricciones_tecnicas, ensure_ascii=False)}

- Archivos del proyecto (paths) para acotar applies_to:
{json.dumps(spec.get("files", []), ensure_ascii=False)}

SALIDA (JSON)
Devuelve EXCLUSIVAMENTE JSON válido con:
{{
  "restrictions": [
    {{
      "id": "string_corto",
      "applies_to": ["*.py"], 
      "must_not_contain": ["substr1", "..."],
      "must_contain_any": ["substrA", "..."]
    }}
  ]
}}

REGLAS IMPORTANTES (GENÉRICAS)
- No inventes tecnologías no mencionadas en las restricciones.
- Solo genera restricciones que se puedan validar de forma razonable con substrings o regex.
- Usa patrones REALISTAS de código/config para detectar incumplimientos (imports, funciones típicas, variables de entorno, dependencias).
- Minimiza falsos positivos: preferir pocos patrones pero discriminativos.
- Si una restricción es conceptual y no se puede validar por patrones simples, omítela.

SOPORTE REGEX (IMPORTANTE)
- En must_contain_any y must_not_contain puedes usar items que empiecen por `re:` para expresar regex.
- Usa regex SOLO cuando haya variaciones sintácticas habituales (ej. imports equivalentes en Python).

- EVITA restricciones demasiado amplias que rompen el proyecto:
  - No generes must_not_contain con tokens genéricos como: "import", "from", "def", "class".
  - “/health sin dependencias externas” NO significa “sin imports”: significa sin SDKs/clientes externos, sin red, sin llamadas a servicios.
- `must_contain_any` significa: al menos UNA de esas substrings/patrones debe aparecer en algún archivo que aplique.
- `must_not_contain` significa: ninguna de esas substrings/patrones debe aparecer.

REGLA (GENÉRICA) - EVITAR RESTRICCIONES INÚTILES/FRÁGILES
- NO generes restricciones que dupliquen validaciones ya hechas por el sistema (imports internos, paths, sintaxis).
- NO generes restricciones con tokens genéricos o estructurales del lenguaje (ej: "import", "from", "def", "class", comillas, docstrings).
- Si una restricción requiere tolerar variaciones sintácticas, usa `re:`. Si no, usa substring.
""".strip()
    for _ in range(max(1, intentos)):
        raw = chat_completion_json(
            prompt=prompt,
            system=None,
            temperature=0.1,
            max_tokens=700,
            fase="generacion_codigo",
        )
        data = extraer_json_tolerante(raw) or {}
        rs = data.get("restrictions")
        if isinstance(rs, list):
            out = []
            for r in rs:
                if not isinstance(r, dict):
                    continue
                rid = str(r.get("id") or "").strip()
                if not rid:
                    continue
                out.append(
                    {
                        "id": rid,
                        "applies_to": r.get("applies_to")
                        if isinstance(r.get("applies_to"), list)
                        else ["*.py"],
                        "must_not_contain": r.get("must_not_contain")
                        if isinstance(r.get("must_not_contain"), list)
                        else [],
                        "must_contain_any": r.get("must_contain_any")
                        if isinstance(r.get("must_contain_any"), list)
                        else [],
                    }
                )

            # Filtro de seguridad post-proceso:
            # - elimina reglas con tokens absurdamente genéricos (falsos positivos)
            # - elimina reglas que se aplicarían a TODO el código Python pero exigen patrones específicos de una integración
            #   (p.ej. obligar a "google.auth.default()" en health.py/__init__.py/main.py).
            banned = {"import", "from", "def", "class", '"""', "'''", '"', "'"}
            cleaned: List[dict] = []

            def _is_overbroad_applies_to(applies_to_val: Any) -> bool:
                if not isinstance(applies_to_val, list) or not applies_to_val:
                    return True
                norm = [str(x).strip() for x in applies_to_val if str(x).strip()]
                return norm == ["*"] or norm == ["*.py"] or "*" in norm

            def _looks_integration_specific(patterns: List[str]) -> bool:
                # Heurística genérica: si exige un token con puntos/paréntesis típico de una API
                # (ej. "google.auth.default()", "drive.permissions().create("), eso NO debe exigirse
                # sobre todo el proyecto; solo sobre módulos de integración.
                for p in patterns:
                    s = str(p)
                    if s.startswith("re:"):
                        s = s[3:]
                    if "." in s and "(" in s:
                        return True
                return False

            def _has_structural_must_contain(patterns: List[str]) -> bool:
                # Patrones demasiado “estructurales” para exigirlos como must_contain_any.
                # Ej: forzar "from app" en todos los módulos provoca falsos positivos masivos.
                structural = ("from app", "import app", "app.")
                for p in patterns:
                    s = str(p).lower()
                    if any(tok in s for tok in structural):
                        return True
                return False

            for rr in out:
                if not isinstance(rr, dict):
                    continue

                must_not = rr.get("must_not_contain") or []
                must_any = rr.get("must_contain_any") or []
                applies_to = rr.get("applies_to")

                if isinstance(must_not, list) and any(
                    str(x).strip().lower() in banned for x in must_not
                ):
                    continue

                # Descarta reglas must_contain_any overbroad con patrones estructurales (falsos positivos)
                if (
                    must_any
                    and isinstance(must_any, list)
                    and _is_overbroad_applies_to(applies_to)
                    and _has_structural_must_contain(must_any)
                ):
                    continue

                # Si es una regla overbroad y además exige patrones específicos -> descartar (falsos positivos)
                if (
                    must_any
                    and isinstance(must_any, list)
                    and _is_overbroad_applies_to(applies_to)
                    and _looks_integration_specific(must_any)
                ):
                    continue

                # Normalización genérica: si la regla intenta imponer "imports absolutos" con must_contain_any,
                # la convertimos a validación negativa (más robusta y compatible con __init__.py vacíos).
                rid2 = str(rr.get("id") or "").strip().lower()
                if rid2 in (
                    "use-absolute-imports",
                    "use_absolute_imports",
                    "absolute-imports",
                ):
                    rr["must_contain_any"] = []
                    rr["must_not_contain"] = list(
                        set(
                            (rr.get("must_not_contain") or [])
                            + [
                                "from services",
                                "import services",
                                "from endpoints",
                                "import endpoints",
                                "from config",
                                "import config",
                            ]
                        )
                    )

                cleaned.append(rr)

            return sanitizar_restrictions(cleaned)
    return []
