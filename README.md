```json

POC - LabBorrow-API
=================

Descripción General
------------------

Este proyecto es una PoC de implementación del sistema LabBorrow, diseñado para gestionar el préstamo y devolución de materiales técnicos entre alumnos y profesores. El objetivo es controlar la disponibilidad del inventario y permitir un uso eficiente del mismo en la academia.

Estructura General
-----------------

La aplicación se encuentra dividida en diferentes módulos funcionales, agrupados según su responsabilidad:

```json
    .
    ├── app/
    |  ├── controllers/        # Módulo de controladores para API
    |  └── routers/           # Ruteador de endpoints FastAPI
    |
    ├── models/               # Modelos del sistema (entidades y relaciones)
    |
    ├── services/            # Módulos de servicios para la persistencia de datos e interacciones con el servidor de bases de datos
    |
    └── core/                # Módulo principal, contiene la implementación del FastAPI y los negociadores de negocios (business rules)
```
Instalación
----------

Para instalar todo el entorno necesario para trabajar en este proyecto se deben seguir los siguientes pasos:

1. Crear un ambiente virtual con Python 3.9 o superior. En nuestro caso, se usarán las versiones 3.9 y 3.8. Puedes encontrar más información aquí: https://realpython.com/python-virtual-environments-the-quick-guide/.
2. Crear un nuevo proyecto con Git. Por ejemplo: `git init`
3. Clonar el repositorio del proyecto en tu carpeta de trabajo. En nuestro caso, se usará la URL: "https://github.com/YOUR_GITHUB_USERNAME/labborrow-api