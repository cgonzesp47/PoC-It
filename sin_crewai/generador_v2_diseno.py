"""
NUEVA ARQUITECTURA V2 (ESTRATEGIA RESILIENTE)
=============================================

En vez de generar un JSON grande complejo en una sola respuesta,
generamos partes pequeñas y construimos el JSON nosotros.

Esto reduce:
- Fallos de formato
- Truncamientos
- Respuestas vacías
- Tiempo total

Estrategia:
1) Generar entidades (JSON pequeño)
2) Generar endpoints (JSON pequeño)
3) Construir design final en Python
"""

import json
import ollama


# ============================================================
# UTILIDAD JSON SEGURA
# ============================================================

def _extraer_json_seguro(texto: str):
    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio != -1 and fin != -1:
        texto = texto[inicio:fin + 1]
    return texto.strip()


def _llm_json(prompt: str, max_tokens: int = 800):
    response = ollama.chat(
        model="qwen7b:latest",
        messages=[{
            "role": "user",
            "content": prompt + "\n\nResponde SOLO con JSON válido."
        }],
        options={
            "temperature": 0.0,
            "num_predict": max_tokens
        }
    )

    contenido = response["message"]["content"]
    contenido = _extraer_json_seguro(contenido)

    return json.loads(contenido)


# ============================================================
# FASE 1 – DISEÑO ESTRUCTURADO RESILIENTE
# ============================================================

def generar_diseno_estructurado(nombre_proyecto: str,
                                objetivo: str,
                                funcionalidades: str,
                                restricciones: str,
                                tecnologias: str) -> dict:

    print("  > [V2] Generando diseño estructurado (modo resiliente)...")

    # --------------------------------------------------------
    # 1️⃣ ENTIDADES
    # --------------------------------------------------------

    prompt_entidades = f"""
A partir de esta descripción:

OBJETIVO:
{objetivo}

FUNCIONALIDADES:
{funcionalidades}

Genera un JSON con esta estructura:

{{
  "entities": {{
    "NombreEntidad": {{
      "fields": {{
        "campo": "tipo"
      }}
    }}
  }},
  "schemas": {{
    "NombreSchema": ["campo1", "campo2"]
  }}
}}
"""

    try:
        parte_modelos = _llm_json(prompt_entidades, 900)
    except Exception:
        raise RuntimeError("No se pudieron generar entidades de forma válida.")

# Endpoints

    prompt_endpoints = f"""
Basándote en estas funcionalidades:

{funcionalidades}

Genera un JSON con esta estructura:

{{
  "endpoints": [
    {{
      "method": "POST|GET|PUT|DELETE",
      "path": "/ruta",
      "request_schema": "SchemaOpcional",
      "response_schema": "SchemaOpcional",
      "description": "texto corto"
    }}
  ]
}}
"""

    try:
        parte_endpoints = _llm_json(prompt_endpoints, 900)
    except Exception:
        raise RuntimeError("No se pudieron generar endpoints de forma válida.")

# Construccion final

    # Normalización defensiva (el agente puede devolver tipos inesperados)
    entidades = parte_modelos.get("entities", {})
    schemas = parte_modelos.get("schemas", {})
    endpoints = parte_endpoints.get("endpoints", [])

    if not isinstance(entidades, dict):
        entidades = {}

    if not isinstance(schemas, dict):
        schemas = {}

    if not isinstance(endpoints, list):
        endpoints = []

    design = {
        "entities": entidades,
        "schemas": schemas,
        "endpoints": endpoints,
        "storage": "in_memory",
        "error_simulation": True
    }

    if not design["endpoints"]:
        raise RuntimeError("Diseño inválido: sin endpoints.")

    if not design["entities"] and not design["schemas"]:
        raise RuntimeError("Diseño inválido: sin entidades ni schemas.")

    print("  [OK] Diseño estructurado construido correctamente.")
    return design


# FASE 2 – GENERACIÓN DETERMINISTA

def generar_schemas_desde_diseno(design: dict) -> str:
    lineas = ["from pydantic import BaseModel\n"]

    schemas = design.get("schemas", {})
    if not isinstance(schemas, dict):
        return "from pydantic import BaseModel\n"

    for schema_name, campos in schemas.items():
        lineas.append(f"\nclass {schema_name}(BaseModel):")
        for campo in campos:
            lineas.append(f"    {campo}: str")
        if not campos:
            lineas.append("    pass")

    return "\n".join(lineas)


def generar_models_desde_diseno(design: dict) -> str:
    lineas = ["from pydantic import BaseModel\n"]

    entidades = design.get("entities", {})
    if not isinstance(entidades, dict):
        return "from pydantic import BaseModel\n"

    for entity_name, entity_data in entidades.items():
        lineas.append(f"\nclass {entity_name}(BaseModel):")
        for campo, tipo in entity_data.get("fields", {}).items():
            lineas.append(f"    {campo}: {tipo}")
        if not entity_data.get("fields"):
            lineas.append("    pass")

    return "\n".join(lineas)


# FASE 3 – API, TESTS & MAIN (LLM CONTROLADO)

def generar_tests_desde_diseno(nombre_proyecto: str, design: dict) -> str:
    """
    Generación determinista de tests basada exclusivamente en los endpoints del design.
    No usa LLM. No hardcodea lógica específica del dominio.
    Deriva dinámicamente:
    - Métodos HTTP
    - Paths
    - Path params
    - Query params
    - request_schema
    - error_simulation
    """

    endpoints = design.get("endpoints", [])
    error_simulation = design.get("error_simulation", False)

    lineas = [
        "import pytest",
        "from fastapi.testclient import TestClient",
        "from main import app",
        "",
        "client = TestClient(app)",
        "",
    ]

    # Detectar endpoint de creación (POST sin path param)
    endpoint_creacion = None
    for ep in endpoints:
        if ep.get("method") == "POST" and "{" not in ep.get("path", ""):
            endpoint_creacion = ep
            break

    # Si hay endpoint de creación, crear fixture dinámico
    if endpoint_creacion:
        path = endpoint_creacion["path"]
        request_schema = endpoint_creacion.get("request_schema")

        lineas.extend([
            "",
            "@pytest.fixture",
            "def created_resource():",
        ])

        if request_schema and isinstance(request_schema, str):
            # Construir payload genérico basado en schema
            campos = design.get("schemas", {}).get(request_schema, [])
            if not isinstance(campos, list):
                campos = []
            payload_line = "{"
            for campo in campos:
                payload_line += f'"{campo}": "test_{campo}", '
            payload_line = payload_line.rstrip(", ") + "}"

            lineas.append(f"    response = client.post('{path}', json={payload_line})")
        else:
            lineas.append(f"    response = client.post('{path}')")

        lineas.append("    assert response.status_code in (200, 201)")
        lineas.append("    return response.json()")
        lineas.append("")

    # Generar tests por endpoint
    for ep in endpoints:
        method = ep.get("method")
        path = ep.get("path")
        func_name = f"test_{method.lower()}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"

        lineas.append(f"def {func_name}():")
        
        # Construir path con valores dummy si hay path params
        test_path = path
        if "{" in path:
            import re
            params = re.findall(r"{(.*?)}", path)
            for p in params:
                test_path = test_path.replace(f"{{{p}}}", "1")

        if method == "GET":
            lineas.append(f"    response = client.get('{test_path}')")
        elif method == "POST":
            request_schema = ep.get("request_schema")
            if request_schema and isinstance(request_schema, str):
                campos = design.get("schemas", {}).get(request_schema, [])
                if not isinstance(campos, list):
                    campos = []
                payload_line = "{"
                for campo in campos:
                    payload_line += f'"{campo}": "test_{campo}", '
                payload_line = payload_line.rstrip(", ") + "}"
                lineas.append(f"    response = client.post('{test_path}', json={payload_line})")
            else:
                lineas.append(f"    response = client.post('{test_path}')")
        elif method == "PUT":
            lineas.append(f"    response = client.put('{test_path}')")
        elif method == "DELETE":
            lineas.append(f"    response = client.delete('{test_path}')")

        lineas.append("    assert response.status_code < 500")
        lineas.append("")

        # Test error_simulation si aplica
        if error_simulation and method == "GET":
            lineas.append(f"def test_{method.lower()}_force_error():")
            lineas.append(f"    response = client.get('{test_path}?force_error=500')")
            lineas.append("    assert response.status_code == 500")
            lineas.append("")

    return "\n".join(lineas) + "\n"

def generar_main_desde_diseno(nombre_proyecto: str, design: dict, tecnologias: str) -> str:
    """
    Ensamblaje determinista de main.py.
    Eliminamos dependencia del LLM para evitar contaminación estructural.
    """

    return (
        "from fastapi import FastAPI\n"
        "from app.api import router\n\n"
        "app = FastAPI()\n"
        "app.include_router(router)\n"
    )


def generar_api_desde_diseno(nombre_proyecto: str, design: dict) -> str:
    """
    Generación estructurada de app/api.py en 4 fases:
    1) Generar JSON con estructura de funciones
    2) Generar firma de cada endpoint
    3) Generar cuerpo de cada endpoint
    4) Ensamblar determinísticamente
    """

    # FASE 1 — ESTRUCTURA DE FUNCIONES (JSON)

    prompt_estructura = f"""
Basándote EXCLUSIVAMENTE en este design:

{json.dumps(design, indent=2)}

Devuelve SOLO un JSON con esta estructura:

{{
  "functions": [
    {{
      "name": "nombre_funcion",
      "method": "GET|POST|PUT|DELETE",
      "path": "/ruta",
      "request_schema": "SchemaOpcional",
      "has_path_params": true|false,
      "has_query_params": true|false
    }}
  ]
}}

NO generes código.
SOLO JSON válido.
"""

    estructura = _llm_json(prompt_estructura, 800)
    funciones = estructura.get("functions", [])

    # FASE 2 — GENERAR FIRMAS

    import ast

    firmas = {}

    for f in funciones:
        intentos = 0
        firma_valida = False
        firma = ""

        while intentos < 3 and not firma_valida:

            prompt_firma = f"""
Genera SOLO la firma de función FastAPI (sin cuerpo) para este endpoint:

{json.dumps(f, indent=2)}

Reglas:
- No generes decoradores.
- No generes cuerpo.
- No generes texto explicativo.
- Devuelve SOLO una línea que empiece por 'def ' y termine con ':'.
"""

            response = ollama.chat(
                model="qwen7b:latest",
                messages=[{"role": "user", "content": prompt_firma}],
                options={"temperature": 0.2, "num_predict": 200}
            )

            firma = response["message"]["content"]
            firma = firma.replace("```python", "").replace("```", "").strip()

            # Limpieza defensiva: eliminar líneas no válidas
            lineas = [l for l in firma.splitlines() if l.strip().startswith("def ")]
            if lineas:
                firma = lineas[0].strip()

            # Validación sintáctica de firma
            try:
                codigo_prueba = firma + "\n    pass"
                ast.parse(codigo_prueba)
                firma_valida = True
            except SyntaxError:
                intentos += 1

        if not firma_valida:
            raise RuntimeError(f"No se pudo generar firma válida para {f['name']}")

        firmas[f["name"]] = firma

    # FASE 3 — GENERAR CUERPOS

    import ast

    cuerpos = {}

    for f in funciones:
        intentos = 0
        cuerpo_valido = False
        cuerpo = ""

        while intentos < 3 and not cuerpo_valido:

            contexto_global = ""
            if design.get("storage") == "in_memory":
                contexto_global += "Variable global disponible: storage = {}\\n"

            contexto_global += "Schemas disponibles:\\n"
            for schema_name in design.get("schemas", {}).keys():
                contexto_global += f"- {schema_name}\\n"

            prompt_cuerpo = f"""
Genera SOLO el cuerpo de la función (sin def, sin decorador).

Firma actual:
{firmas[f["name"]]}

Contexto disponible:
{contexto_global}

Design completo:
{json.dumps(design, indent=2)}

REGLAS ESTRICTAS:
- No generes def.
- No generes decorador.
- No generes texto explicativo.
- No generes listas con '-'.
- No generes comentarios.
- No generes funciones auxiliares.
- Usa únicamente variables y schemas disponibles.
- Devuelve SOLO código Python válido.
"""

            response = ollama.chat(
                model="qwen7b:latest",
                messages=[{"role": "user", "content": prompt_cuerpo}],
                options={"temperature": 0.2, "num_predict": 800}
            )

            cuerpo = response["message"]["content"]
            cuerpo = cuerpo.replace("```python", "").replace("```", "").strip()

            # Validación sintáctica
            try:
                codigo_prueba = "def _temp():\n"
                codigo_prueba += "\n".join("    " + l for l in cuerpo.splitlines())
                ast.parse(codigo_prueba)
                cuerpo_valido = True
            except SyntaxError:
                intentos += 1

        if not cuerpo_valido:
            # Fallback determinista mínimo seguro
            cuerpos[f["name"]] = "raise HTTPException(status_code=500, detail='Auto-generated fallback')"
        else:
            cuerpos[f["name"]] = cuerpo

    # FASE 4 — ENSAMBLAJE DETERMINISTA

    header = [
        "from fastapi import APIRouter, HTTPException",
        "from typing import Optional",
        "",
        "router = APIRouter()",
        ""
    ]

    if design.get("storage") == "in_memory":
        header.append("storage = {}")
        header.append("")

    bloques = []

    for f in funciones:
        nombre = f["name"]
        decorador = f'@router.{f["method"].lower()}("{f["path"]}")'
        bloques.append(decorador)
        bloques.append(firmas[nombre])
        cuerpo_indentado = "\n".join("    " + linea for linea in cuerpos[nombre].splitlines())
        bloques.append(cuerpo_indentado)
        bloques.append("")

    return "\n".join(header + bloques).strip() + "\n"


# UTILIDAD

def guardar_diseno(nombre_proyecto: str, design: dict):
    ruta = f"output/{nombre_proyecto}/design_v2.json"
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(design, f, indent=2)
    print(f"  [V2] Diseño guardado en {ruta}")
