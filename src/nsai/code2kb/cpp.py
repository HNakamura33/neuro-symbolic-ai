"""C/C++ extractor (tree-sitter `cpp` grammar — parses C too).

Entity naming: module = root-relative path (base.path_module_name);
functions/classes are qualified as ``<module>.<ns>.<Class>.<method>`` where
namespaces/classes are syntactically visible. ``#include "a/b.h"`` → imports
``a.b``; ``#include <vector>`` → imports ``vector``.

For higher precision (USR-based resolution, overloads), see the optional
libclang backend in `clang.py`.
"""

from __future__ import annotations

from pathlib import Path

from .base import path_module_name

EXTENSIONS = {".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"}

module_name = path_module_name


def extract(source: str, module: str, path: Path | None = None) -> list[tuple[str, str, str]]:
    raise NotImplementedError("C/C++ extractor not implemented yet")  # TODO(agent:cpp)
