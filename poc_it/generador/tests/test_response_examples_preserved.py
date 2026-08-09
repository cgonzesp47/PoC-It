from __future__ import annotations

from poc_it.generador.request_ir import build_request_ir_from_context
from poc_it.generador.spec_builder import build_spec_from_request_ir


def test_response_json_example_list_preserved_from_context_to_spec() -> None:
    contexto = {
        "nombre_proyecto": "PoC",
        "objetivo_tecnico": "demo",
        "contratos_api_explicitos": [
            {
                "method": "GET",
                "path": "/productos",
                "request": {"type": "none", "evidence": "usuario: GET /productos"},
                "response": {"json_example": [{"id": 1, "name": "A"}], "evidence": "usuario: devuelve lista"},
            }
        ],
    }

    ir = build_request_ir_from_context(contexto, descripcion_global="")
    assert ir.explicit_api_contracts
    assert isinstance(ir.explicit_api_contracts[0].response_example, list)

    spec = build_spec_from_request_ir(ir)
    ep = next(e for e in spec["endpoints"] if e["path"] == "/productos")
    assert isinstance(ep["response"]["json_example"], list)
    assert ep["response"]["json_example"] == [{"id": 1, "name": "A"}]


def test_response_json_example_list_preserved_for_get_productos() -> None:
    contexto = {
        "nombre_proyecto": "PoC",
        "objetivo_tecnico": "demo",
        "contratos_api_explicitos": [
            {
                "method": "GET",
                "path": "/productos",
                "request": {"type": "none", "evidence": "usuario: GET /productos"},
                "response": {"json_example": [{"id": 1}, {"id": 2}], "evidence": "usuario: devuelve lista"},
            }
        ],
    }

    ir = build_request_ir_from_context(contexto, descripcion_global="")
    spec = build_spec_from_request_ir(ir)

    ep = next(e for e in spec["endpoints"] if e["method"] == "GET" and e["path"] == "/productos")
    assert isinstance(ep["response"]["json_example"], list)
    assert ep["response"]["json_example"] == [{"id": 1}, {"id": 2}]
