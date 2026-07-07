"""libclang C/C++ backend (`nsai.code2kb.clang`).

Fixtures are deliberately self-contained (a local header + a .cpp including
it, no system includes): the bundled libclang wheel does not reliably locate
platform stdlib headers, and the backend must extract user code regardless of
missing-header diagnostics.
"""

from pathlib import Path

import pytest

from nsai.code2kb import extract_from_path
from nsai.code2kb import clang as clang_backend

pytest.importorskip("clang.cindex", reason="libclang extra not installed")


def _write_fixture(tmp_path: Path) -> Path:
    """A local header with the base class + a .cpp exercising the tricky cases."""
    src = tmp_path / "src"
    (src / "animals").mkdir(parents=True)
    (src / "animals" / "animal.h").write_text(
        "#pragma once\n"
        "namespace app {\n"
        "class Animal {\n"
        "public:\n"
        "  virtual void speak();\n"
        "};\n"
        "class BarkError {};\n"
        "}\n"
    )
    (src / "dog.cpp").write_text(
        '#include "animals/animal.h"\n'
        "namespace app {\n"
        "class Dog : public Animal {\n"
        "public:\n"
        "  void bark();\n"
        "  void speak() {}\n"
        "};\n"
        "class Trainer {\n"
        "public:\n"
        "  void train(Dog& d) { d.bark(); }\n"
        "};\n"
        "// out-of-line definition: must qualify via semantic_parent\n"
        "void Dog::bark() {\n"
        "  speak();\n"
        "  throw BarkError();\n"
        "}\n"
        "}\n"
    )
    return src


def test_extract_precise_triples(tmp_path: Path):
    src = _write_fixture(tmp_path)
    cpp = src / "dog.cpp"
    triples = clang_backend.extract(
        cpp.read_text(), clang_backend.module_name(cpp, src), path=cpp
    )

    assert triples[0] == ("dog", "rdf:type", "Module")
    assert ("dog", "imports", "animals.animal") in triples

    assert ("dog.app.Dog", "rdf:type", "Class") in triples
    assert ("dog.app.Dog", "defined_in", "dog") in triples
    assert ("dog.app.Trainer", "rdf:type", "Class") in triples
    # base resolved through the actual declaration in the included header
    # (defined outside this file → bare qualified name, dots for ::)
    assert ("dog.app.Dog", "rdfs:subClassOf", "app.Animal") in triples

    # out-of-line `void Dog::bark()` qualified via semantic_parent — the
    # precision win over tree-sitter, which only sees `Dog::bark` syntactically
    assert ("dog.app.Dog.bark", "rdf:type", "Function") in triples
    assert ("dog.app.Dog.bark", "defined_in", "dog") in triples
    assert ("dog.app.Dog.speak", "rdf:type", "Function") in triples

    # unqualified speak() inside the out-of-line body resolves to the exact
    # qualified method of the enclosing class
    assert ("dog.app.Dog.bark", "calls", "dog.app.Dog.speak") in triples
    # cross-class method call resolves through the receiver's type
    assert ("dog.app.Trainer.train", "calls", "dog.app.Dog.bark") in triples

    # throw site: exception type declared in the header → bare qualified name
    assert ("dog.app.Dog.bark", "raises", "app.BarkError") in triples

    # deduplicated
    assert len(triples) == len(set(triples))


def test_extract_without_path_and_c_mode(tmp_path: Path):
    # path=None: parsed via unsaved-file machinery with a synthetic filename
    triples = clang_backend.extract(
        "namespace n { void f(); void g() { f(); } }", "mod"
    )
    assert ("mod", "rdf:type", "Module") in triples
    assert ("mod.n.g", "rdf:type", "Function") in triples
    assert ("mod.n.g", "defined_in", "mod") in triples
    assert ("mod.n.g", "calls", "mod.n.f") in triples
    # declaration-only f is not emitted as a Function
    assert ("mod.n.f", "rdf:type", "Function") not in triples

    # .c files are parsed as C11
    c_file = tmp_path / "util.c"
    c_file.write_text("static int helper(void) { return 1; }\nint run(void) { return helper(); }\n")
    c_triples = clang_backend.extract(c_file.read_text(), "util", path=c_file)
    assert ("util.run", "rdf:type", "Function") in c_triples
    assert ("util.run", "calls", "util.helper") in c_triples


def test_broken_code_does_not_raise():
    # clang recovers from parse errors; extraction still yields what parsed
    triples = clang_backend.extract("void ok() {}\nclass {{{", "broken")
    assert ("broken", "rdf:type", "Module") in triples
    assert ("broken.ok", "rdf:type", "Function") in triples


def test_extract_from_path_with_clang_backend(tmp_path: Path):
    src = _write_fixture(tmp_path)
    results = {f.name: t for f, t in extract_from_path(src, cpp_backend="clang")}

    assert set(results) == {"animal.h", "dog.cpp"}
    # header scanned as its own module
    assert ("animals.animal.app.Animal", "rdf:type", "Class") in results["animal.h"]
    assert ("dog.app.Dog.bark", "calls", "dog.app.Dog.speak") in results["dog.cpp"]
    assert ("dog.app.Dog", "rdfs:subClassOf", "app.Animal") in results["dog.cpp"]


def test_cli_build_from_code_clang(tmp_path: Path):
    from typer.testing import CliRunner

    from nsai.cli import app

    src = _write_fixture(tmp_path)
    kb_path = tmp_path / "kb.ttl"
    result = CliRunner().invoke(
        app,
        ["kb", "build-from-code", str(src), "--cpp-backend", "clang", "--kb", str(kb_path)],
    )
    assert result.exit_code == 0, result.output
    assert "scanned 2 files" in result.output

    from nsai.kb import KnowledgeBase

    store = KnowledgeBase(kb_path)
    assert ("ns:dog.app.Dog.bark", "ns:calls", "ns:dog.app.Dog.speak") in store.find(
        predicate="ns:calls"
    )
