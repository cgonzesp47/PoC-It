"""
Módulo para integración con Git.
Inicializa repositorio y crea commit inicial.
"""
import subprocess
import os


def inicializar_git(nombre_proyecto: str) -> bool:
    """
    Inicializa un repositorio Git en el directorio del proyecto.
    
    Args:
        nombre_proyecto: Nombre del proyecto
        
    Returns:
        bool: True si se inicializó correctamente, False en caso contrario
    """
    
    directorio = f"output/{nombre_proyecto}"
    
    print(f"  > Inicializando repositorio Git en {directorio}...")
    
    try:
        # Inicializar repositorio
        subprocess.run(
            ['git', 'init'],
            cwd=directorio,
            check=True,
            capture_output=True
        )
        print("    [OK] Repositorio inicializado")
        
        # Crear .gitignore si no existe
        gitignore_path = os.path.join(directorio, '.gitignore')
        if not os.path.exists(gitignore_path):
            gitignore_content = """# Python
                                __pycache__/
                                *.py[cod]
                                *$py.class
                                *.so
                                .Python
                                venv/
                                env/
                                ENV/
                                .venv

                                # IDEs
                                .vscode/
                                .idea/
                                *.swp
                                *.swo

                                # Environment
                                .env
                                .env.local

                                # FastAPI
                                .pytest_cache/
            """
            with open(gitignore_path, 'w', encoding='utf-8') as f:
                f.write(gitignore_content)
            print("    [OK] .gitignore creado")
        
        # Añadir todos los archivos
        subprocess.run(
            ['git', 'add', '.'],
            cwd=directorio,
            check=True,
            capture_output=True
        )
        print("    [OK] Archivos añadidos al stage")
        
        # Crear commit inicial
        subprocess.run(
            ['git', 'commit', '-m', f'Initial commit: {nombre_proyecto} PoC'],
            cwd=directorio,
            check=True,
            capture_output=True
        )
        print("    [OK] Commit inicial creado")
        
        print(f"  [OK] Repositorio Git configurado correctamente")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"  [ERROR] Error al ejecutar git: {e}")
        return False
    except Exception as e:
        print(f"  [ERROR] Error inesperado: {e}")
        return False


def obtener_estado_git(nombre_proyecto: str) -> str:
    """
    Obtiene el estado del repositorio Git.
    
    Args:
        nombre_proyecto: Nombre del proyecto
        
    Returns:
        str: Estado del repositorio
    """
    
    directorio = f"output/{nombre_proyecto}"
    
    try:
        result = subprocess.run(
            ['git', 'status', '--short'],
            cwd=directorio,
            check=True,
            capture_output=True,
            text=True
        )
        return result.stdout.strip() or "Working tree clean"
    except Exception:
        return "No git repository"
