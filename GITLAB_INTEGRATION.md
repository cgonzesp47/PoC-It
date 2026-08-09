# GitLab Integration – Publicación Automática de PoCs

Este documento describe la configuración necesaria para que el sistema **publique automáticamente** cada PoC generada en un nuevo repositorio de GitLab.

---

# 🎯 ¿Qué hace esta funcionalidad?

Cuando el flujo del sistema termina correctamente (modo:

- ✅ PARCIAL  
- ✅ COMPLETO  
- ✅ ASESOR  

), el sistema:

1. Crea un nuevo repositorio en GitLab.
2. Lo crea dentro del grupo configurado (por defecto: `FORTEFORTE`).
3. Sube todo el contenido generado en `output/PoCXXX`.
4. Muestra por consola la URL del repositorio creado.

⚠️ El sistema **sigue guardando todo en `output/` como hasta ahora**.  
La publicación en GitLab es una funcionalidad adicional.

---

# 🔐 Variables de entorno necesarias

Para que funcione correctamente deben existir estas variables:

```
POCIT_GITLAB_TOKEN
POCIT_GITLAB_GROUP
POCIT_GITLAB_URL
```

---

## ✅ 1. POCIT_GITLAB_TOKEN

Token personal de GitLab con permisos:

- `api`
- `write_repository`

Ejemplo de creación (PowerShell):

```powershell
setx POCIT_GITLAB_TOKEN "tu_token_aqui"
```

---

## ✅ 2. POCIT_GITLAB_GROUP

Grupo donde se crearán los repositorios.

Actualmente configurado como:

```
FORTEFORTE
```

Si en el futuro quieres cambiar el grupo, simplemente ejecuta:

```powershell
setx POCIT_GITLAB_GROUP "NOMBRE_NUEVO_GRUPO"
```

Y reinicia la terminal.

---

## ✅ 3. POCIT_GITLAB_URL

URL base de tu GitLab corporativo.

Ejemplo actual:

```
https://umane.emeal.nttdata.com
```

Si cambias de entorno (por ejemplo, a otro GitLab), solo modifica esta variable:

```powershell
setx POCIT_GITLAB_URL "https://nuevo-gitlab.com"
```

---

# 🛑 ¿Cómo desactivar la publicación automática?

Si en algún momento no quieres que el sistema publique automáticamente:

Puedes eliminar el token:

```powershell
setx POCIT_GITLAB_TOKEN ""
```

O directamente borrar la variable desde:

```
Sistema → Variables de entorno
```

Sin token, la publicación no se ejecutará.

---

# 🧠 Buenas prácticas

✅ No subir este token a ningún repositorio.  
✅ No incluir el token en archivos `.env` versionados.  
✅ Usar siempre variables de entorno del sistema.  

---

# 📌 Resumen rápido

| Variable | Descripción |
|----------|------------|
| POCIT_GITLAB_TOKEN | Token personal con permisos API |
| POCIT_GITLAB_GROUP | Grupo destino (ej. FORTEFORTE) |
| POCIT_GITLAB_URL | URL base del GitLab |

---

# 🚀 Resultado esperado

Tras una ejecución correcta del sistema, deberías ver en consola algo como:

```
======================================
REPOSITORIO PUBLICADO EN GITLAB
======================================

URL: https://umane.emeal.nttdata.com/FORTEFORTE/PoC123
```

---

Documento generado automáticamente como parte de la integración GitLab.
