"""TypeScript/JavaScript extractor (tree-sitter `typescript` / `tsx` grammars).

Entity naming: module = root-relative path (base.path_module_name), so
``src/utils/foo.ts`` → ``src.utils.foo``. Relative imports are resolved
against the importing file (``import x from './foo'`` in ``src/bar.ts`` →
imports ``src.foo``); bare specifiers are recorded as the package name.
``extends`` and ``implements`` both map to rdfs:subClassOf.
"""

from __future__ import annotations

from pathlib import Path

from .base import path_module_name

EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

module_name = path_module_name


def extract(source: str, module: str, path: Path | None = None) -> list[tuple[str, str, str]]:
    raise NotImplementedError("TypeScript extractor not implemented yet")  # TODO(agent:ts)
