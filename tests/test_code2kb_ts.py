from pathlib import Path

from nsai.code2kb import extract_from_path
from nsai.code2kb.typescript import extract, module_name
from nsai.kb import KnowledgeBase


def _write_pkg(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    src = root / "src"
    src.mkdir(parents=True)
    (src / "base.ts").write_text(
        "export class Animal {\n"
        "  speak(): string {\n"
        "    throw new Error('not implemented');\n"
        "  }\n"
        "}\n"
    )
    (src / "dog.ts").write_text(
        "import { Animal } from './base';\n"
        "import { EventEmitter } from 'node:events';\n"
        "import chalk from 'chalk';\n"
        "\n"
        "export class Dog extends Animal implements Walker {\n"
        "  speak(): string {\n"
        "    return this.bark();\n"
        "  }\n"
        "\n"
        "  bark(): string {\n"
        "    return chalk.red('woof');\n"
        "  }\n"
        "}\n"
        "\n"
        "interface Walker {\n"
        "  walk(): void;\n"
        "}\n"
        "\n"
        "export function adopt(name: string): Dog {\n"
        "  if (!name) {\n"
        "    throw new RangeError('no name');\n"
        "  }\n"
        "  return new Dog();\n"
        "}\n"
        "\n"
        "const greet = (dog: Dog) => {\n"
        "  console.log(adopt('rex'));\n"
        "};\n"
    )
    # a decoy that must be skipped by the directory walk
    vendored = root / "node_modules" / "chalk"
    vendored.mkdir(parents=True)
    (vendored / "index.js").write_text("export function red(s) { return s; }\n")
    return root


def test_module_name_is_root_relative(tmp_path: Path):
    root = _write_pkg(tmp_path)
    assert module_name(root / "src" / "dog.ts", root) == "src.dog"
    assert module_name(root / "src" / "base.ts", root) == "src.base"


def test_extract_structural_triples(tmp_path: Path):
    root = _write_pkg(tmp_path)
    file = root / "src" / "dog.ts"
    triples = extract(file.read_text(), "src.dog", path=file)

    assert ("src.dog", "rdf:type", "Module") in triples
    # relative import resolved against the importing module's path
    assert ("src.dog", "imports", "src.base") in triples
    # bare specifiers recorded as package names
    assert ("src.dog", "imports", "node:events") not in triples  # ':' fails NAME_RE
    assert ("src.dog", "imports", "chalk") in triples
    assert ("src.dog.Dog", "rdf:type", "Class") in triples
    assert ("src.dog.Dog", "defined_in", "src.dog") in triples
    # base class resolved through the import binding (cross-file)
    assert ("src.dog.Dog", "rdfs:subClassOf", "src.base.Animal") in triples
    # implements also maps to rdfs:subClassOf, resolved to the local interface
    assert ("src.dog.Dog", "rdfs:subClassOf", "src.dog.Walker") in triples
    assert ("src.dog.Walker", "rdf:type", "Class") in triples
    assert ("src.dog.Dog.speak", "rdf:type", "Function") in triples
    assert ("src.dog.adopt", "defined_in", "src.dog") in triples
    # this.bark() resolved to the enclosing class; new Dog() to the local class
    assert ("src.dog.Dog.speak", "calls", "src.dog.Dog.bark") in triples
    assert ("src.dog.adopt", "calls", "src.dog.Dog") in triples
    # unresolvable external call recorded verbatim
    assert ("src.dog.Dog.bark", "calls", "chalk.red") in triples
    assert ("src.dog.adopt", "raises", "RangeError") in triples
    # top-level arrow assignment is a function; its calls resolve locally
    assert ("src.dog.greet", "rdf:type", "Function") in triples
    assert ("src.dog.greet", "calls", "src.dog.adopt") in triples


def test_relative_import_walks_up_and_requires(tmp_path: Path):
    source = (
        "import { helper } from '../util/helper.ts';\n"
        "import * as base from './base';\n"
        "const fs = require('fs');\n"
        "require('./boot');\n"
        "class Pup extends base.Animal {}\n"
    )
    triples = extract(source, "src.pets.dog", path=Path("src/pets/dog.js"))
    # '../util/helper.ts' walks up from src/pets and drops the extension
    assert ("src.pets.dog", "imports", "src.util.helper") in triples
    assert ("src.pets.dog", "imports", "src.pets.base") in triples
    assert ("src.pets.dog", "imports", "fs") in triples
    assert ("src.pets.dog", "imports", "src.pets.boot") in triples
    # namespace import binding resolves the heritage expression (js grammar)
    assert ("src.pets.dog.Pup", "rdfs:subClassOf", "src.pets.base.Animal") in triples


def test_scoped_package_and_tsx(tmp_path: Path):
    source = (
        "import { Button } from '@scope/pkg';\n"
        "export function App() {\n"
        "  return <Button onClick={() => render()} />;\n"
        "}\n"
    )
    triples = extract(source, "src.app", path=Path("src/app.tsx"))
    assert ("src.app", "imports", "scope.pkg") in triples
    assert ("src.app.App", "rdf:type", "Function") in triples


def test_extract_from_path_and_kb_roundtrip(tmp_path: Path):
    root = _write_pkg(tmp_path)
    (root / "src" / "broken.ts").write_text("class {{{ function ((\n")

    store = KnowledgeBase(tmp_path / "kb.ttl")
    results = {file: triples for file, triples in extract_from_path(root)}

    # node_modules is skipped entirely
    assert not any("node_modules" in str(file) for file in results)
    # tree-sitter degrades on bad syntax instead of crashing
    broken = results[root / "src" / "broken.ts"]
    assert ("src.broken", "rdf:type", "Module") in broken

    for file, triples in results.items():
        if triples:
            store.add_triples(triples, source=f"{file} (static-analysis)")

    # facts are queryable and carry provenance
    assert ("ns:src.dog.Dog", "ns:defined_in", "ns:src.dog") in store.find(
        predicate="ns:defined_in"
    )
    records = store.provenance("src.dog.Dog", "defined_in", "src.dog")
    assert len(records) == 1
    assert records[0]["source"].endswith("dog.ts (static-analysis)")


def test_subclass_feeds_owl_inference(tmp_path: Path):
    root = _write_pkg(tmp_path)
    store = KnowledgeBase(tmp_path / "kb.ttl")
    for _, triples in extract_from_path(root):
        if triples:
            store.add_triples(triples)
    # rdfs:subClassOf from static analysis participates in OWL-RL reasoning:
    # an instance of Dog is entailed to be an Animal.
    store.add_triples([("ns:rex", "rdf:type", "ns:src.dog.Dog")])
    result = store.verify_triple("ns:rex", "rdf:type", "ns:src.base.Animal")
    assert result.verdict == "entailed"
