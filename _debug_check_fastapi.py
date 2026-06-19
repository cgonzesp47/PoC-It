import os
import sys

print("=== DEBUG PYTHON ENV ===")
print("sys.executable:", sys.executable)
print("sys.version:", sys.version.replace("\n", " "))
print("cwd:", os.getcwd())
print("PYTHONPATH:", os.getenv("PYTHONPATH"))
print("VIRTUAL_ENV:", os.getenv("VIRTUAL_ENV"))
print("PATH(head):", os.getenv("PATH", "")[:200])
print("sys.path[0:5]:", sys.path[:5])

print("\n=== DEBUG IMPORTS ===")
try:
    import fastapi

    print("fastapi:", fastapi.__version__, "from", getattr(fastapi, "__file__", None))
except Exception as e:
    print("fastapi import FAILED:", repr(e))

try:
    import pytest

    print("pytest:", pytest.__version__, "from", getattr(pytest, "__file__", None))
except Exception as e:
    print("pytest import FAILED:", repr(e))
