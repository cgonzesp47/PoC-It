# Contract-first codegen

Estado final: existe una única ruta productiva de generación en `poc_it.materializacion.generador_artefactos`, delegando en `poc_it.materializacion.codegen` la generación y validación por archivo.

## Flujo

1. **Validar SPEC**
   - `poc_it.generador.spec_validation`
   - `poc_it.generador.spec_traceability`

2. **Construir Implementation Contracts**
   - `poc_it.generador.implementation_contracts`

3. **Planificar archivos**
   - `poc_it.generador.file_planner`
   - completa `__init__.py` necesarios y fija el conjunto de paths permitidos

4. **Construir File Contracts**
   - `poc_it.generador.file_contracts`

5. **Validar File Contracts**
   - `poc_it.generador.file_contracts_validation`
   - este es el gate previo a codegen

6. **Interfaces**
   - `codegen.models.GeneratedFile`
   - `codegen.models.CodegenIssue`
   - `codegen.file_generator.FileContractGenerator`
   - `codegen.orchestrator.ContractFirstCodegen`

7. **Generación individual**
   - `ContractFirstCodegen` genera archivo a archivo a partir de cada File Contract
   - el parseo de respuesta vive en `codegen.parsing`

8. **Validación estructural por archivo**
   - única fuente: `codegen.structural_validation.validate_generated_file_structure`
   - agrega checks de path, contenido vacío, sintaxis Python y símbolos requeridos

9. **Validación estructural por lote de archivos**
   - única fuente: `codegen.structural_validation.validate_generated_files_against_file_contracts`
   - recorre todos los File Contracts y aplica la validación estructural archivo por archivo

10. **Validación semántica por archivo y de proyecto**
    - `codegen.semantic_validation`
    - `codegen.project_validation`
    - aquí viven las reglas de consistencia funcional y de integración entre archivos

11. **Validación cruzada**
    - única fuente productiva: `codegen.project_validation.validate_generated_project`
    - comprueba coherencia entre archivos generados, contratos y SPEC

12. **Repair**
    - repair semántico/cross-file: `codegen.semantic_repair.repair_generated_project`
    - repair de imports: `poc_it.generador.repair_loop.aplicar_repair_loop_imports`
    - guardrails finales: `poc_it.generador.guardrails`

13. **Clasificación de resultado**
    - `generador_artefactos.py` devuelve:
      - `valid`
      - `generated_with_errors`
      - `file_contract_validation_failed`
      - `failed`

## Principios de arquitectura

- `generador_artefactos.py` actúa como **orquestador de alto nivel**.
- El parsing JSON de respuesta de codegen no se implementa ahí; se delega en `codegen.parsing`.
- La validación estructural no se duplica; la fuente única está en `codegen.structural_validation`.
- La validación semántica y cruzada no se duplica; viven en `codegen.semantic_validation` y `codegen.project_validation`.
- No se usan prompts batch legacy para materialización productiva.
- Los tests de arquitectura verifican que no reaparezcan imports legacy.

## Tests herméticos

Los tests de `poc_it/materializacion/tests` validan:

- orden de generación por contratos
- parseo de respuesta por archivo
- validación estructural
- validación semántica
- validación cruzada de proyecto
- repair semántico
- ausencia de imports legacy

Estos tests deben permanecer sin llamadas externas.

## Componentes activos

- `poc_it/materializacion/generador_artefactos.py`
- `poc_it/materializacion/codegen/orchestrator.py`
- `poc_it/materializacion/codegen/file_generator.py`
- `poc_it/materializacion/codegen/parsing.py`
- `poc_it/materializacion/codegen/structural_validation.py`
- `poc_it/materializacion/codegen/semantic_validation.py`
- `poc_it/materializacion/codegen/project_validation.py`
- `poc_it/materializacion/codegen/semantic_repair.py`

## Resultado esperado

- una sola ruta productiva de codegen
- cero dependencia productiva de `prompts_lotes`
- cero validadores estructurales duplicados en producción
- orquestación separada de parsing, validación y repair
- suite hermética capaz de bloquear regresiones de arquitectura
