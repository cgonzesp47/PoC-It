
# LabBorrow-API

## DESCRIPCIÓN
El sistema LabBorrow-API es una aplicación web que se enfoca en la gestión del préstamo y devolución de material técnico (osciloscopios, placas de desarrollo, multímetros) entre alumnos y profesores, mientras controla la disponibilidad del inventario. El sistema permite al usuario dar de alta nuevos materiales, obtener un listado actualizado de los elementos disponibles para préstamo, solicitar prestamos, registrar devoluciones y controlar el número máximo de objetos en prestamo por cada usuario.

## INSTALACIÓN
1. Crea un entorno virtual utilizando `venv` y abre la consola del mismo.
2. Instala las dependencias con `poetry install`.
3. Sigue cualquier configuración adicional si es necesaria.

## EJECUCIÓN
1. Inicia el servidor FastAPI ejecutando el comando `uvicorn main:app --host 0.0.0.0 --port 8000`.
2. El sistema estará disponible en la URL <http://localhost:8000>.
3. Para ver la documentación interactiva, utiliza el comando `curl http://localhost:8000/docs`

## ESTRUCTURA DEL PROYECTO
```yaml
fastapi-labborrow-api
│ 
├── main.py
├── README.md
├── requirements.txt
└── src
    ├── main.py
    ├── services
    │   ├── user.py
    │   └── material.py
    ├── models
    │   ├── user.py
    │   └── material.py
    ├── routers
    │   ├── user_router.py
    │   └── material_router.py
    ├── utils
    │   ├── database.py
    │   ├── auth.py
    │   └── upload.py
    └── tests
        ├── conftest.py
        └── test_user.py
```
## ENTIDADES Y REGLAS DE NEGOCIO
- **Material** (ID, nombre, categoría, estado) - Regla de negocio: Material no puede estar marcado como "en uso".
- **Usuario** (ID, nombre, rol) - Regla de negocio: Un usuario no puede tener más de 2 materiales prestados simultáneamente.