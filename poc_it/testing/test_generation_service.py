from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from typing import Any, Dict, List, Optional

from poc_it.materializacion.materializador_archivos import materializar_proyecto
from poc_it.testing.domain.enums import TestStrategy
from poc_it.testing.domain.models import TestGenerationResult, build_file_artifacts
from poc_it.testing.planning.semantic_plan_merger import (
    SemanticMergeResult,
    SemanticPlanValidationError,
    merge_semantic_enrichment,
)
from poc_it.testing.planning.test_plan_builder import build_test_plan_for_generation
from poc_it.testing.rendering.suite_renderer import render_suite_from_plan
from poc_it.testing.semantic_enrichment import (
    SemanticEnrichmentContext,
    SemanticEnrichmentService,
    SemanticEnrichmentValidationError,
)

logger = logging.getLogger(__name__)

DEFAULT_FEATURE_FLAG = "POC_IT_USE_TEST_GENERATION_SERVICE"


class TestGenerationService:
    __test__ = False

    """
    Punto de entrada único interno para la generación de tests.

    Dependencias dirigidas:
    domain <- planning <- rendering
                     -> execution
    """

    def __init__(
        self,
        *,
        feature_flag_enabled: Optional[bool] = None,
        fallback_to_legacy_minimal: bool = True,
        semantic_enrichment_service: Optional[SemanticEnrichmentService] = None,
    ) -> None:
        if feature_flag_enabled is None:
            raw = os.getenv(DEFAULT_FEATURE_FLAG, "1").strip().lower()
            feature_flag_enabled = raw not in {"0", "false", "off", "no"}
        self.feature_flag_enabled = bool(feature_flag_enabled)
        self.fallback_to_legacy_minimal = bool(fallback_to_legacy_minimal)
        self.semantic_enrichment_service = semantic_enrichment_service or SemanticEnrichmentService()

    def generate(
        self,
        *,
        project_structure: Dict[str, str],
        runtime_contracts: Optional[dict],
        runtime_facts: Optional[dict],
        spec: Optional[dict] = None,
        mode: Optional[str] = None,
        project_name: str = "TmpProject",
    ) -> TestGenerationResult:
        if not self.feature_flag_enabled:
            return self._generate_minimal_fallback(
                warning="feature flag disabled; using minimal fallback path",
            )

        normalized_mode = self._resolve_mode(spec=spec, mode=mode)

        try:
            planning_result = build_test_plan_for_generation(
                project_structure=project_structure or {},
                runtime_contracts=runtime_contracts if isinstance(runtime_contracts, dict) else None,
                runtime_facts=runtime_facts if isinstance(runtime_facts, dict) else None,
                spec=spec if isinstance(spec, dict) else None,
                mode=normalized_mode,
                project_name=project_name,
            )

            enrichment_result = self._safe_semantic_enrichment(
                runtime_contracts=runtime_contracts if isinstance(runtime_contracts, dict) else None,
                runtime_facts=runtime_facts if isinstance(runtime_facts, dict) else None,
                spec=spec if isinstance(spec, dict) else None,
                project_name=project_name,
            )

            plan_warnings: List[str] = []
            merged_plan = planning_result.plan
            merged_structure_with_plan = dict(planning_result.structure_with_plan)

            try:
                semantic_merge_result = merge_semantic_enrichment(
                    plan=planning_result.plan,
                    enrichment=enrichment_result,
                    runtime_contracts=runtime_contracts or {},
                )
                if isinstance(semantic_merge_result, SemanticMergeResult):
                    merged_plan = semantic_merge_result.plan
                    plan_warnings.extend(semantic_merge_result.warnings)
                else:
                    merged_plan = semantic_merge_result
                if hasattr(merged_plan, "to_dict"):
                    merged_structure_with_plan[".poc_it/test_plan.json"] = (
                        json.dumps(merged_plan.to_dict(), ensure_ascii=False, indent=2) + "\n"
                    )
            except SemanticPlanValidationError as exc:
                logger.info("[TESTS] Semantic plan merge rejected: %s", exc)
                plan_warnings.append(f"semantic enrichment ignored: {exc}")
                merged_plan = planning_result.plan
            except Exception as exc:
                logger.info("[TESTS] Semantic plan merge failed: %s", exc)
                plan_warnings.append(f"semantic enrichment ignored: {exc}")
                merged_plan = planning_result.plan

            suite_result = render_suite_from_plan(
                structure_with_plan=merged_structure_with_plan,
                runtime_contracts=runtime_contracts if isinstance(runtime_contracts, dict) else None,
                runtime_facts=runtime_facts if isinstance(runtime_facts, dict) else None,
                plan=merged_plan,
                project_name=project_name,
            )

            patch: Dict[str, str] = dict(suite_result.patch)
            patch[".poc_it/test_plan.json"] = merged_structure_with_plan.get(".poc_it/test_plan.json", "")
            patch[".poc_it/test_validation_report.json"] = json.dumps(
                suite_result.validation_report, ensure_ascii=False, indent=2
            ) + "\n"
            patch[".poc_it/semantic_enrichment.json"] = json.dumps(
                {
                    "semantic_cases": enrichment_result.semantic_cases,
                    "stateful_scenarios": enrichment_result.stateful_scenarios,
                    "dependency_behaviors": enrichment_result.dependency_behaviors,
                    "expected_interactions": enrichment_result.expected_interactions,
                    "uncertainties": enrichment_result.uncertainties,
                    "warnings": enrichment_result.warnings,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n"

            return TestGenerationResult(
                ok=True,
                strategy=TestStrategy.CONTRACT_FIRST.value,
                structure_patch=patch,
                test_plan=merged_plan,
                validation_report=suite_result.validation_report,
                file_artifacts=build_file_artifacts(list(patch.keys()), TestStrategy.CONTRACT_FIRST),
                warnings=[*list(enrichment_result.warnings), *plan_warnings],
            )
        except Exception as exc:
            logger.info("[TESTS] Error in TestGenerationService contract-first path: %s", exc)
            if not self.fallback_to_legacy_minimal:
                return TestGenerationResult(
                    ok=False,
                    strategy=TestStrategy.CONTRACT_FIRST.value,
                    structure_patch={},
                    warnings=[str(exc)],
                )
            result = self._generate_minimal_fallback(
                warning=f"contract-first failed: {exc}",
            )
            return result

    def materialize_result(
        self,
        *,
        nombre_proyecto: str,
        result: TestGenerationResult,
        estructura_destino: Dict[str, str],
        archivos_creados: List[str],
        resultado: Optional[Dict[str, Any]] = None,
    ) -> bool:
        archivos_tests = materializar_proyecto(
            nombre_proyecto=nombre_proyecto,
            estructura=result.structure_patch,
            limpiar_directorio=False,
        )
        archivos_creados.extend(archivos_tests)
        estructura_destino.update(result.structure_patch)

        self._asegurar_requirements_dev(estructura_destino, resultado or {})
        req_files_to_materialize: Dict[str, str] = {}
        if "requirements-dev.txt" in estructura_destino:
            req_files_to_materialize["requirements-dev.txt"] = estructura_destino["requirements-dev.txt"]
        if "requirements.txt" in estructura_destino:
            req_files_to_materialize["requirements.txt"] = estructura_destino["requirements.txt"]

        if req_files_to_materialize:
            archivos_req = materializar_proyecto(
                nombre_proyecto=nombre_proyecto,
                estructura=req_files_to_materialize,
                limpiar_directorio=False,
            )
            archivos_creados.extend(archivos_req)

        return True

    def _generate_minimal_fallback(self, *, warning: str) -> TestGenerationResult:
        patch = {
            "pytest.ini": "[pytest]\naddopts = -q\ntestpaths = tests\n",
            "tests/__init__.py": "",
            "tests/test_startup.py": (
                "def test_import_app_main():\n"
                "    import importlib\n\n"
                "    mod = importlib.import_module('app.main')\n"
                "    assert mod is not None\n"
            ),
        }
        return TestGenerationResult(
            ok=True,
            strategy=TestStrategy.MINIMAL_FALLBACK.value,
            structure_patch=patch,
            file_artifacts=build_file_artifacts(list(patch.keys()), TestStrategy.MINIMAL_FALLBACK),
            warnings=[warning],
        )

    def _resolve_mode(self, *, spec: Optional[dict], mode: Optional[str]) -> str:
        m = str(mode or "").strip().upper()
        if m:
            return m
        if isinstance(spec, dict):
            spec_mode = str(spec.get("modo") or spec.get("mode") or "").strip().upper()
            if spec_mode:
                return spec_mode
        return "PARCIAL"

    def _normalizar_reqs(self, lines: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for ln in lines:
            s = (ln or "").strip()
            if not s or s.startswith("#"):
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    def _asegurar_requirements_dev(self, estructura: Dict[str, str], resultado: Dict[str, Any]) -> None:
        spec = resultado.get("spec") if isinstance(resultado, dict) else None
        dev_deps = []
        if isinstance(spec, dict):
            dd = spec.get("dev_dependencies")
            if isinstance(dd, list):
                dev_deps = [str(x) for x in dd if str(x).strip()]

        if not dev_deps:
            dev_deps = ["pytest", "pytest-mock", "httpx", "pytest-json-report", "python-multipart"]

        dev_deps.extend(["pytest-json-report", "python-multipart"])
        dev_deps = self._normalizar_reqs(dev_deps)

        existing = (estructura.get("requirements-dev.txt") or "").strip()
        if not existing:
            estructura["requirements-dev.txt"] = "\n".join(dev_deps) + "\n"

    def _safe_semantic_enrichment(
        self,
        *,
        runtime_contracts: Optional[dict],
        runtime_facts: Optional[dict],
        spec: Optional[dict],
        project_name: str,
    ):
        context = self._build_semantic_context(
            runtime_contracts=runtime_contracts,
            runtime_facts=runtime_facts,
            spec=spec,
            project_name=project_name,
        )
        try:
            return self.semantic_enrichment_service.enrich(context)
        except SemanticEnrichmentValidationError as exc:
            logger.info("[TESTS] Semantic enrichment rejected: %s", exc)
            fallback = self.semantic_enrichment_service.__class__().enrich(context)
            return replace(
                fallback,
                warnings=[
                    *list(fallback.warnings),
                    f"semantic enrichment ignored: {exc}",
                ],
            )
        except Exception as exc:
            logger.info("[TESTS] Semantic enrichment failed: %s", exc)
            fallback = self.semantic_enrichment_service.__class__().enrich(context)
            return replace(
                fallback,
                warnings=[
                    *list(fallback.warnings),
                    f"semantic enrichment ignored: {exc}",
                ],
            )

    def _build_semantic_context(
        self,
        *,
        runtime_contracts: Optional[dict],
        runtime_facts: Optional[dict],
        spec: Optional[dict],
        project_name: str,
    ) -> SemanticEnrichmentContext:
        normalized_openapi = self._normalize_openapi(spec, runtime_contracts)
        endpoints = runtime_contracts.get("endpoints") if isinstance(runtime_contracts, dict) else []
        endpoints = endpoints if isinstance(endpoints, list) else []

        handlers: Dict[str, str] = {}
        dependencies: Dict[str, List[str]] = {}
        observed_methods: Dict[str, List[str]] = {}
        related_models: Dict[str, List[str]] = {}
        deterministic_cases: Dict[str, List[Dict[str, Any]]] = {}
        allowed_dependency_overrides = set()
        if isinstance(runtime_contracts, dict):
            allowed_dependency_overrides = {
                str(value).strip()
                for value in (runtime_contracts.get("allowed_dependency_overrides") or [])
                if str(value).strip()
            }

        for endpoint in endpoints:
            if not isinstance(endpoint, dict):
                continue
            path = str(endpoint.get("path") or "").strip()
            method = str(endpoint.get("method") or "").lower().strip()
            if not path or not method:
                continue
            operation_id = str(endpoint.get("operation_id") or f"{method}:{path}")
            handlers[operation_id] = str(endpoint.get("handler_source") or endpoint.get("handler_name") or "")
            dependencies[operation_id] = [
                str(value).strip()
                for value in (endpoint.get("depends_imports") or [])
                if str(value).strip()
            ]
            observed_methods[operation_id] = [
                str(value).strip()
                for value in (endpoint.get("observed_methods") or endpoint.get("dependency_methods") or [])
                if str(value).strip()
            ]
            related_models[operation_id] = [
                str(value).strip()
                for value in (endpoint.get("pydantic_models") or endpoint.get("related_models") or [])
                if str(value).strip()
            ]
            deterministic_case = {
                "expected_status": endpoint.get("status_code"),
                "sample_request": endpoint.get("sample_request"),
                "response_keys": endpoint.get("response_json_required_keys") or [],
            }
            deterministic_cases[operation_id] = [deterministic_case]

            endpoint_dependencies = dependencies[operation_id]
            if endpoint_dependencies and any(dep in allowed_dependency_overrides for dep in endpoint_dependencies):
                auth_dependency = any(
                    token in dep.lower()
                    for dep in endpoint_dependencies
                    for token in ("auth", "current_user", "token", "security")
                )
                if auth_dependency:
                    deterministic_cases[operation_id].append(
                        {
                            "expected_status": endpoint.get("status_code"),
                            "sample_request": endpoint.get("sample_request"),
                            "response_keys": endpoint.get("response_json_required_keys") or [],
                            "headers": {
                                "authorization": "Bearer ok",
                            },
                            "dependency_setup": [
                                {
                                    "dependency_fqn": dep,
                                    "method_name": "__call__",
                                    "action": "return",
                                    "value": {"sub": "demo"},
                                }
                                for dep in endpoint_dependencies
                                if dep in allowed_dependency_overrides
                            ],
                        }
                    )

        if not handlers and isinstance(normalized_openapi.get("paths"), dict):
            for path, methods in normalized_openapi["paths"].items():
                if not isinstance(methods, dict):
                    continue
                for method, definition in methods.items():
                    if not isinstance(definition, dict):
                        continue
                    operation_id = str(definition.get("operationId") or f"{str(method).lower()}:{path}")
                    handlers[operation_id] = ""
                    dependencies[operation_id] = []
                    observed_methods[operation_id] = []
                    related_models[operation_id] = []
                    deterministic_cases[operation_id] = []

        return SemanticEnrichmentContext(
            scope_id=project_name,
            normalized_openapi=normalized_openapi,
            handlers=handlers,
            dependencies=dependencies,
            observed_methods=observed_methods,
            related_models=related_models,
            deterministic_cases=deterministic_cases,
        )

    def _normalize_openapi(self, spec: Optional[dict], runtime_contracts: Optional[dict]) -> Dict[str, Any]:
        if isinstance(spec, dict):
            if isinstance(spec.get("paths"), dict):
                return spec
            if isinstance(spec.get("openapi"), dict):
                return spec["openapi"]
        if isinstance(runtime_contracts, dict):
            for key in ("openapi", "openapi_json", "openapi_spec"):
                candidate = runtime_contracts.get(key)
                if isinstance(candidate, dict) and isinstance(candidate.get("paths"), dict):
                    return candidate
        return {"paths": {}}
