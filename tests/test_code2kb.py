from pathlib import Path

from nsai.code2kb import extract_from_path, extract_module_triples, module_name_for
from nsai.kb import KnowledgeBase


def _write_pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "base.py").write_text(
        "class Animal:\n"
        "    def speak(self):\n"
        "        raise NotImplementedError\n"
    )
    (pkg / "dog.py").write_text(
        "import json\n"
        "from .base import Animal\n"
        "\n"
        "class Dog(Animal):\n"
        "    def speak(self):\n"
        "        return self.bark()\n"
        "\n"
        "    def bark(self):\n"
        "        return json.dumps('woof')\n"
        "\n"
        "def adopt(name):\n"
        "    if not name:\n"
        "        raise ValueError('no name')\n"
        "    return Dog()\n"
    )
    return pkg


def test_module_name_walks_packages(tmp_path: Path):
    pkg = _write_pkg(tmp_path)
    assert module_name_for(pkg / "dog.py") == "pkg.dog"
    assert module_name_for(pkg / "__init__.py") == "pkg"
    standalone = tmp_path / "script.py"
    standalone.write_text("")
    assert module_name_for(standalone) == "script"


def test_extract_structural_triples(tmp_path: Path):
    pkg = _write_pkg(tmp_path)
    triples = extract_module_triples((pkg / "dog.py").read_text(), "pkg.dog")

    assert ("pkg.dog", "rdf:type", "Module") in triples
    assert ("pkg.dog", "imports", "json") in triples
    # relative import resolved against the package
    assert ("pkg.dog", "imports", "pkg.base") in triples
    assert ("pkg.dog.Dog", "rdf:type", "Class") in triples
    assert ("pkg.dog.Dog", "defined_in", "pkg.dog") in triples
    # base class resolved through the from-import alias
    assert ("pkg.dog.Dog", "rdfs:subClassOf", "pkg.base.Animal") in triples
    assert ("pkg.dog.Dog.speak", "rdf:type", "Function") in triples
    assert ("pkg.dog.adopt", "defined_in", "pkg.dog") in triples
    # self.bark() resolved to the enclosing class; Dog() to the local class
    assert ("pkg.dog.Dog.speak", "calls", "pkg.dog.Dog.bark") in triples
    assert ("pkg.dog.adopt", "calls", "pkg.dog.Dog") in triples
    # unresolvable external call recorded verbatim
    assert ("pkg.dog.Dog.bark", "calls", "json.dumps") in triples
    assert ("pkg.dog.adopt", "raises", "ValueError") in triples


def test_extract_from_path_and_kb_roundtrip(tmp_path: Path):
    pkg = _write_pkg(tmp_path)
    (pkg / "broken.py").write_text("def oops(:\n")

    store = KnowledgeBase(tmp_path / "kb.ttl")
    results = dict(extract_from_path(pkg))
    assert results[pkg / "broken.py"] == []  # parse error → empty, not a crash

    for file, triples in results.items():
        if triples:
            store.add_triples(triples, source=f"{file} (static-analysis)")

    # facts are queryable and carry provenance
    assert ("ns:pkg.dog.Dog", "ns:defined_in", "ns:pkg.dog") in store.find(
        predicate="ns:defined_in"
    )
    records = store.provenance("pkg.dog.Dog", "defined_in", "pkg.dog")
    assert len(records) == 1
    assert records[0]["source"].endswith("dog.py (static-analysis)")

    # re-running is idempotent: no duplicate triples added
    added_again = sum(
        store.add_triples(t, source="rerun") for t in results.values() if t
    )
    assert added_again == 0


def test_subclass_feeds_owl_inference(tmp_path: Path):
    pkg = _write_pkg(tmp_path)
    store = KnowledgeBase(tmp_path / "kb.ttl")
    for _, triples in extract_from_path(pkg):
        if triples:
            store.add_triples(triples)
    # rdfs:subClassOf from static analysis participates in OWL-RL reasoning:
    # an instance of Dog is entailed to be an Animal.
    store.add_triples([("ns:rex", "rdf:type", "ns:pkg.dog.Dog")])
    result = store.verify_triple("ns:rex", "rdf:type", "ns:pkg.base.Animal")
    assert result.verdict == "entailed"
