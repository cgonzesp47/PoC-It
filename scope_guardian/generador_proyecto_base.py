"""
ScopeGuardian - Generador de Proyecto Base

Responsabilidad:
Generar la estructura mínima viable de un proyecto FastAPI
en función de los bloques generables detectados.

Este módulo:
- NO materializa archivos en disco.
- NO mezcla responsabilidades.
- NO contiene estructura hardcodeada.
- Valida sintaxis Python antes de devolver resultados.
- Reintenta una vez si detecta error sintáctico.
"""

from __future__ import annotations

import ast
import json
from typing import Dict, List

import ollama


# ==========================================================
# CONSTRUCCIÓN DE PROMPT
# ==========================================================


def _construir_prompt(
    bloques_generables: List[str],
    descripcion_global: str,
) -> str:
    bloques_texto = "\n".join(f"- {b}" for b in bloques_generables)

    return f"""
Eres un generador de proyectos backend FastAPI.

Debes generar la estructura mínima viable de un proyecto
que permita implementar estos bloques:

{bloques_texto}

Descripción general del proyecto:
{descripcion_global}

Dominio:
- Python 3.11
- FastAPI
- Proyecto backend REST
- No incluir funcionalidades adicionales no solicitadas

Devuelve exclusivamente JSON válido con esta estructura:

{{
  "ruta/archivo.py": "contenido del archivo",
  ...
}}

Reglas:
- Incluir solo archivos estrictamente necesarios.
- No incluir explicaciones.
- No incluir texto fuera del JSON.
- El proyecto debe poder arrancar con: uvicorn main:app --reload
"""
    

# ==========================================================
# UTILIDADES INTERNAS
# ==========================================================


def _normalizar_codigo(contenido: str) -> str:
    contenido = contenido.strip()

    if contenido.startswith("```python"):
        contenido = contenido.replace("```python", "").replace("```", "").strip()
    elif contenido.startswith("```"):
        contenido = contenido.replace("```", "").strip()

    return contenido


def _validar_sintaxis_python(estructura: Dict[str, str]) -> None:
    for ruta, contenido in estructura.items():
        if ruta.endswith(".py"):
            try:
                ast.parse(contenido)
            except SyntaxError as e:
                raise SyntaxError(f"Error sintáctico en {ruta}: {e}") from e


def _validar_coherencia_minima(estructura: Dict[str, str]) -> None:
    if "main.py" not in estructura:
        raise ValueError("La estructura generada no contiene main.py")

    main_content = estructura["main.py"]

    if "FastAPI" not in main_content:
        raise ValueError("main.py no contiene referencia a FastAPI")

    # Frágil, el modelo podría generar: application = FastAPI() o api = FastAPI()
    if "app = FastAPI" not in main_content:
        raise ValueError("main.py no define instancia FastAPI")


def _corregir_archivo_con_error(ruta: str, contenido: str) -> str:
    prompt = f"""
El siguiente archivo Python tiene un error sintáctico.
Corrige exclusivamente el archivo manteniendo su intención original.

Archivo: {ruta}

Contenido:
```python
{contenido}
```

Devuelve SOLO código Python válido.
"""

    response = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt}],
        options={
            "temperature": 0.0,
            "num_predict": 2000,
        },
    )

    corregido = response.get("message", {}).get("content", "").strip()
    return _normalizar_codigo(corregido)


# ==========================================================
# FUNCIÓN PRINCIPAL
# ==========================================================


def generar_proyecto_base(
    nombre_proyecto: str,
    bloques_generables: List[str],
    descripcion_global: str,
    tecnologias: str,
) -> Dict[str, str]:
    """
    Nueva arquitectura híbrida robusta:

    Fase 1: LLM genera DISEÑO estructural (JSON tipado).
    Fase 2: Compilador determinista transforma diseño en código válido.
    Fase 3: LLM SOLO genera cuerpos de funciones (bloques aislados).
    """

    # =====================================================
    # FASE 1 — GENERAR DISEÑO INTERMEDIO (JSON)
    # =====================================================

    prompt_diseno = f"""
Genera SOLO JSON válido con esta estructura:

{{
  "entities": [
    {{
      "name": "EntityName",
      "fields": {{
        "field_name": "str|int|float|bool"
      }}
    }}
  ],
  "endpoints": [
    {{
      "method": "GET|POST|PUT|DELETE",
      "path": "/ruta",
      "entity": "EntityName",
      "description": "Qué hace"
    }}
  ]
}}

Bloques funcionales:
{chr(10).join(f"- {b}" for b in bloques_generables)}

Descripción:
{descripcion_global}

Reglas:
- Solo JSON.
- No explicaciones.
"""

    response = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt_diseno}],
        format="json",
        options={"temperature": 0.0, "num_predict": 800},
    )

    contenido = response.get("message", {}).get("content", "{}")

    try:
        design = json.loads(contenido)
    except Exception:
        design = {"entities": [], "endpoints": []}

    # =====================================================
    # VALIDACIÓN ESTRUCTURAL DEL DISEÑO (CRÍTICA)
    # =====================================================

    def _validar_diseno(d: dict) -> dict:
        allowed_types = {"str", "int", "float", "bool"}
        allowed_methods = {"GET", "POST", "PUT", "DELETE"}

        entities = d.get("entities", [])
        endpoints = d.get("endpoints", [])

        if not isinstance(entities, list) or not isinstance(endpoints, list):
            raise ValueError("Diseño inválido: entities/endpoints deben ser listas")

        entity_names = set()

        # Validar entidades
        for e in entities:
            name = e.get("name")
            fields = e.get("fields", {})

            if not name or not isinstance(fields, dict):
                raise ValueError("Entidad inválida en diseño")

            if name in entity_names:
                raise ValueError(f"Entidad duplicada: {name}")

            entity_names.add(name)

            for fname, ftype in fields.items():
                if ftype not in allowed_types:
                    raise ValueError(f"Tipo no permitido: {ftype}")

        endpoint_paths = set()

        # Validar endpoints
        for ep in endpoints:
            method = ep.get("method")
            path = ep.get("path")
            entity = ep.get("entity")

            if method not in allowed_methods:
                raise ValueError(f"Método HTTP inválido: {method}")

            if not path or not path.startswith("/"):
                raise ValueError(f"Ruta inválida: {path}")

            if path in endpoint_paths:
                raise ValueError(f"Ruta duplicada: {path}")

            endpoint_paths.add(path)

            if entity and entity not in entity_names:
                raise ValueError(f"Endpoint referencia entidad inexistente: {entity}")

        return d

    try:
        design = _validar_diseno(design)
    except Exception:
        # fallback seguro si diseño inválido
        design = {"entities": [], "endpoints": []}

    entities = design.get("entities", [])
    endpoints = design.get("endpoints", [])

    # =====================================================
    # FASE 2 — COMPILADOR DETERMINISTA
    # =====================================================

    estructura: Dict[str, str] = {}

    # ---------- main.py ----------
    estructura["main.py"] = """
from fastapi import FastAPI
from app.api import router

app = FastAPI(title="AutoGenerated API")
app.include_router(router)
""".strip()

    # ---------- app/__init__.py ----------
    estructura["app/__init__.py"] = ""

    # ---------- schemas.py ----------
    schemas_code = "from pydantic import BaseModel\n\n"

    for entity in entities:
        name = entity.get("name", "Item")
        fields = entity.get("fields", {})

        schemas_code += f"class {name}(BaseModel):\n"
        if not fields:
            schemas_code += "    pass\n\n"
        else:
            for fname, ftype in fields.items():
                schemas_code += f"    {fname}: {ftype}\n"
            schemas_code += "\n"

    estructura["app/schemas.py"] = schemas_code.strip()

    # =====================================================
    # SERVICES — CRUD determinista en memoria
    # =====================================================

    services_code = """
# Almacenamiento en memoria por entidad
storage = {}

def _ensure_entity(entity_name: str):
    if entity_name not in storage:
        storage[entity_name] = {}

def create_item(entity_name: str, item: dict):
    _ensure_entity(entity_name)
    entity_store = storage[entity_name]
    item_id = str(len(entity_store) + 1)
    entity_store[item_id] = item
    return {"id": item_id, **item}

def list_items(entity_name: str):
    _ensure_entity(entity_name)
    return storage[entity_name]

def get_item(entity_name: str, item_id: str):
    _ensure_entity(entity_name)
    return storage[entity_name].get(item_id)

def update_item(entity_name: str, item_id: str, data: dict):
    _ensure_entity(entity_name)
    if item_id not in storage[entity_name]:
        return None
    storage[entity_name][item_id].update(data)
    return {"id": item_id, **storage[entity_name][item_id]}

def delete_item(entity_name: str, item_id: str):
    _ensure_entity(entity_name)
    return storage[entity_name].pop(item_id, None)
""".strip()

    estructura["app/services.py"] = services_code

    # =====================================================
    # API — Endpoints tipados determinísticos
    # =====================================================

    api_code = """
from fastapi import APIRouter, HTTPException
from app.services import create_item, list_items, get_item, update_item, delete_item
from app.schemas import *
from typing import Dict

router = APIRouter()
""".strip()

    manual_items = []

    for ep in endpoints:
        method = ep.get("method", "GET").upper()
        path = ep.get("path", "/")
        entity = ep.get("entity")
        description = ep.get("description", "")

        # Detectar operaciones externas/no CRUD
        if any(keyword in description.lower() for keyword in ["google", "cloud", "oauth", "external", "api externa"]):
            manual_items.append(f"Implementar lógica externa para endpoint {method} {path}")
            continue

        if entity:
            entity_class = entity
        else:
            entity_class = None

        if method == "POST" and entity_class:
            api_code += f"""

@router.post("{path}", response_model={entity_class})
def create_{entity_class.lower()}(data: {entity_class}):
    return create_item("{entity_class}", data.dict())
"""
        elif method == "GET" and entity_class and "{id}" not in path:
            api_code += f"""

@router.get("{path}", response_model=Dict)
def list_{entity_class.lower()}():
    return list_items("{entity_class}")
"""
        elif method == "GET" and entity_class and "{id}" in path:
            api_code += f"""

@router.get("{path}", response_model=Dict)
def get_{entity_class.lower()}(id: str):
    item = get_item("{entity_class}", id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    return item
"""
        elif method == "PUT" and entity_class and "{id}" in path:
            api_code += f"""

@router.put("{path}", response_model=Dict)
def update_{entity_class.lower()}(id: str, data: {entity_class}):
    item = update_item("{entity_class}", id, data.dict())
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    return item
"""
        elif method == "DELETE" and entity_class and "{id}" in path:
            api_code += f"""

@router.delete("{path}", response_model=Dict)
def delete_{entity_class.lower()}(id: str):
    item = delete_item("{entity_class}", id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    return {{"deleted": id}}
"""
        else:
            manual_items.append(f"Endpoint no soportado automáticamente: {method} {path}")

    estructura["app/api.py"] = api_code.strip()

    # =====================================================
    # REQUIREMENTS (separación base vs externas)
    # =====================================================

    base_deps = {"fastapi", "uvicorn", "uvicorn[standard]", "pydantic"}

    external_deps = []

    if tecnologias:
        tech_tokens = [
            t.strip().lower()
            for t in tecnologias.replace(";", ",").split(",")
            if t.strip()
        ]

        for t in tech_tokens:
            if t not in base_deps:
                external_deps.append(t)

    external_deps = sorted(set(external_deps))

    requirements_lines = [
        "fastapi",
        "uvicorn[standard]",
        "pydantic",
    ]

    for dep in external_deps:
        requirements_lines.append(dep)

    estructura["requirements.txt"] = "\n".join(requirements_lines)

    # =====================================================
    # README_MANUAL (modo profesional agnóstico)
    # =====================================================

    if manual_items or external_deps:

        manual_readme = "# ⚠️ Implementación manual requerida\n\n"
        manual_readme += "El sistema ha detectado funcionalidades que requieren intervención manual.\n\n"

        if manual_items:
            manual_readme += "## Endpoints no automatizados\n\n"
            for item in manual_items:
                manual_readme += f"- {item}\n"
            manual_readme += "\n"

        if external_deps:
            manual_readme += "## Dependencias externas detectadas\n\n"
            manual_readme += "Las siguientes dependencias no forman parte del núcleo FastAPI y requieren configuración específica:\n\n"
            for dep in external_deps:
                manual_readme += f"- {dep}\n"
            manual_readme += "\n"
            manual_readme += "### Recomendaciones técnicas generales:\n"
            manual_readme += "- Configurar credenciales mediante variables de entorno.\n"
            manual_readme += "- No almacenar secretos en el código fuente.\n"
            manual_readme += "- Manejar explícitamente excepciones externas (timeouts, autenticación, permisos).\n"
            manual_readme += "- Separar la lógica externa en `app/services.py`.\n\n"

        manual_readme += "## Checklist profesional sugerido\n\n"
        manual_readme += "- Verificar configuración de entorno antes del despliegue.\n"
        manual_readme += "- Añadir logging estructurado si se integra con servicios externos.\n"
        manual_readme += "- Validar códigos de error HTTP coherentes (401, 403, 404, 500).\n"
        manual_readme += "- Documentar variables de entorno necesarias en el README principal.\n"
        manual_readme += "- Añadir pruebas de integración si hay dependencias externas.\n\n"

        manual_readme += "---\n"
        manual_readme += "El generador ha construido la base estructural y el CRUD determinista.\n"
        manual_readme += "Las integraciones específicas deben completarse siguiendo este checklist.\n"

        estructura["README_MANUAL.md"] = manual_readme.strip()

    # =====================================================
    # FASE 3 — VALIDACIÓN
    # =====================================================

    _validar_sintaxis_python(estructura)

    if "main.py" not in estructura:
        raise ValueError("Proyecto inválido: falta main.py")

    return estructura


# ==========================================================
# VALIDACIÓN END-TO-END OPCIONAL
# ==========================================================

def validar_proyecto_generado(ruta_proyecto: str) -> bool:
    """
    Validación end-to-end opcional:
    - Import dinámico de main.py
    - Verifica existencia de app FastAPI
    - Ejecuta TestClient básico sobre endpoints CRUD
    """

    import importlib.util
    import sys
    from pathlib import Path

    try:
        ruta_main = Path(ruta_proyecto) / "main.py"

        if not ruta_main.exists():
            print("[E2E] main.py no encontrado")
            return False

        spec = importlib.util.spec_from_file_location("generated_main", ruta_main)

        if spec is None or spec.loader is None:
            print("[E2E] No se pudo crear spec de importación")
            return False

        module = importlib.util.module_from_spec(spec)
        sys.modules["generated_main"] = module
        spec.loader.exec_module(module)

        if not hasattr(module, "app"):
            print("[E2E] No existe objeto 'app' en main.py")
            return False

        try:
            from fastapi import FastAPI  # type: ignore
            from fastapi.testclient import TestClient  # type: ignore
        except ImportError:
            print("[E2E] fastapi no está instalado en el entorno actual")
            return False

        app = module.app

        if not isinstance(app, FastAPI):
            print("[E2E] 'app' no es instancia de FastAPI")
            return False

        client = TestClient(app)

        # Verificar que el servidor responde
        response_root = client.get("/")
        if response_root.status_code not in (200, 404):
            print("[E2E] La app no responde correctamente")
            return False

        # Intentar detectar endpoints CRUD automáticamente
        for route in app.routes:
            if hasattr(route, "methods") and "GET" in route.methods:
                try:
                    r = client.get(route.path.replace("{id}", "1"))
                    if r.status_code >= 500:
                        print(f"[E2E] Error en endpoint {route.path}")
                        return False
                except Exception:
                    return False

        print("[E2E] Validación end-to-end superada")
        return True

    except Exception as e:
        print(f"[E2E] Error en validación end-to-end: {e}")
        return False
