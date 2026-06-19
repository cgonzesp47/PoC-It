"""PoC-it package.

This file exists to avoid Python treating `poc_it/` as a namespace package (PEP 420),
which can lead to accidental module shadowing/merging if another `poc_it` directory
exists in sys.path (e.g., from a different checkout or an installed package).
"""
