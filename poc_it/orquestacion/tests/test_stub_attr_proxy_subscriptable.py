from __future__ import annotations

import inspect
import re

import poc_it.orquestacion.runtime_probe as runtime_probe_module


def _load_stub_attr_proxy_class():
    """Extrae y ejecuta la clase `_StubAttrProxy` incrustada en el script del runtime probe.

    El proxy se define dentro de un bloque de código embebido como string local a
    `run_runtime_probe` (se ejecuta en un subproceso separado durante el probe real), así que
    para probarlo unitariamente leemos el fuente del módulo, extraemos la clase por regex y la
    compilamos/ejecutamos en un namespace aislado.
    """
    source = inspect.getsource(runtime_probe_module)
    match = re.search(r"_STUB_SIG = \{.*?\n\n(?=def _override_callable)", source, re.S)
    assert match, "No se encontró _StubAttrProxy (+ _sig_touch) en runtime_probe.py"
    ns: dict = {}
    exec(compile(match.group(0), "<stub_attr_proxy>", "exec"), ns)
    return ns["_StubAttrProxy"]


def test_stub_attr_proxy_supports_dict_style_access():
    """Regresión: código de app perfectamente válido que accede a su dependencia como dict
    (p.ej. `client['folder_id']`, patrón común en clientes de SDKs como google-api-python-client)
    hacía abortar el probe con 'object is not subscriptable', gastando presupuesto de reparación
    LLM intentando arreglar código que no estaba roto."""
    proxy_cls = _load_stub_attr_proxy_class()
    client = proxy_cls("app.integrations.google_drive.build_client")

    folder_id = client["folder_id"]
    assert isinstance(folder_id, proxy_cls)

    # Encadenado dict + atributo + llamada, como en `client['drive_service'].files().create(...).execute()`
    result = client["drive_service"].files().create(body={}, media_body=None, fields="id").execute()
    assert isinstance(result, proxy_cls)
    assert isinstance(result.get("id"), proxy_cls)


def test_stub_attr_proxy_truthiness_and_membership_do_not_crash():
    proxy_cls = _load_stub_attr_proxy_class()
    stub = proxy_cls("dep")
    assert bool(stub) is True
    assert "anything" in stub
    assert list(stub) == []
