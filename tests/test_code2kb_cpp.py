from pathlib import Path

from nsai.code2kb import extract_from_path
from nsai.code2kb.cpp import extract, module_name
from nsai.kb import KnowledgeBase

HEADER = (
    "#pragma once\n"
    "\n"
    "namespace zoo {\n"
    "class Animal {\n"
    "public:\n"
    "    virtual void speak() { }\n"
    "};\n"
    "}\n"
)

CPP = (
    '#include "animals/base.h"\n'
    "#include <vector>\n"
    "\n"
    "namespace zoo {\n"
    "\n"
    "class Dog : public Animal {\n"
    "public:\n"
    "    void bark();\n"
    "    int legs() { return 4; }\n"
    "};\n"
    "\n"
    "void Dog::bark() {\n"
    "    this->speak();\n"
    "    Animal::speak();\n"
    "    log(legs());\n"
    "    throw std::runtime_error(\"woof\");\n"
    "}\n"
    "\n"
    "void adopt() {\n"
    "    Dog d;\n"
    "    d.bark();\n"
    "    external.render();\n"
    "}\n"
    "\n"
    "}\n"
)


def _write_src(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "animals").mkdir(parents=True)
    (src / "animals" / "base.h").write_text(HEADER)
    (src / "dog.cpp").write_text(CPP)
    return src


def test_module_name_is_root_relative_path(tmp_path: Path):
    src = _write_src(tmp_path)
    assert module_name(src / "dog.cpp", src) == "dog"
    assert module_name(src / "animals" / "base.h", src) == "animals.base"


def test_extract_structural_triples():
    triples = extract(CPP, "dog")

    assert triples[0] == ("dog", "rdf:type", "Module")
    assert ("dog", "imports", "animals.base") in triples
    assert ("dog", "imports", "vector") in triples
    # class inside a namespace, base recorded verbatim (Animal lives in the header)
    assert ("dog.zoo.Dog", "rdf:type", "Class") in triples
    assert ("dog.zoo.Dog", "defined_in", "dog") in triples
    assert ("dog.zoo.Dog", "rdfs:subClassOf", "Animal") in triples
    # inline method and out-of-line member both qualified through the class
    assert ("dog.zoo.Dog.legs", "rdf:type", "Function") in triples
    assert ("dog.zoo.Dog.bark", "rdf:type", "Function") in triples
    assert ("dog.zoo.Dog.bark", "defined_in", "dog") in triples
    assert ("dog.zoo.adopt", "rdf:type", "Function") in triples
    # this-> resolved to the enclosing class; A::f becomes a dotted name;
    # a method of the enclosing class called unqualified is class-qualified
    assert ("dog.zoo.Dog.bark", "calls", "dog.zoo.Dog.speak") in triples
    assert ("dog.zoo.Dog.bark", "calls", "Animal.speak") in triples
    assert ("dog.zoo.Dog.bark", "calls", "dog.zoo.Dog.legs") in triples
    # unresolvable external calls recorded verbatim
    assert ("dog.zoo.Dog.bark", "calls", "log") in triples
    assert ("dog.zoo.adopt", "calls", "render") in triples
    # d.bark(): adopt has no enclosing class, so the field name stays verbatim
    assert ("dog.zoo.adopt", "calls", "bark") in triples
    assert ("dog.zoo.Dog.bark", "raises", "std.runtime_error") in triples

    # deduplicated and deterministic
    assert triples == list(dict.fromkeys(triples))
    assert triples == extract(CPP, "dog")


def test_base_class_resolved_when_defined_in_same_file():
    src = HEADER + CPP  # single translation unit defining both classes
    triples = extract(src, "all")
    assert ("all.zoo.Animal", "rdf:type", "Class") in triples
    assert ("all.zoo.Dog", "rdfs:subClassOf", "all.zoo.Animal") in triples
    # A::f resolved against the same-file class too
    assert ("all.zoo.Dog.bark", "calls", "all.zoo.Animal.speak") in triples


def test_garbage_input_degrades_without_crashing():
    triples = extract("class {{{ ~~~ #include <<>> void ()", "junk")
    assert triples[0] == ("junk", "rdf:type", "Module")  # never raises


def test_extract_from_path_and_kb_roundtrip(tmp_path: Path):
    src = _write_src(tmp_path)
    (src / "broken.cpp").write_text("namespace { class ??? !!!")

    store = KnowledgeBase(tmp_path / "kb.ttl")
    results = {file.name: triples for file, triples in extract_from_path(src)}
    assert set(results) == {"base.h", "dog.cpp", "broken.cpp"}
    # tree-sitter degrades instead of raising: module triple always present
    assert ("broken", "rdf:type", "Module") in results["broken.cpp"]
    assert ("animals.base.zoo.Animal", "rdf:type", "Class") in results["base.h"]

    for name, triples in results.items():
        if triples:
            store.add_triples(triples, source=f"{name} (static-analysis)")

    assert ("ns:dog.zoo.Dog", "ns:defined_in", "ns:dog") in store.find(
        predicate="ns:defined_in"
    )
    records = store.provenance("dog.zoo.Dog.bark", "calls", "dog.zoo.Dog.speak")
    assert len(records) == 1
    assert records[0]["source"] == "dog.cpp (static-analysis)"

    # re-running is idempotent: no duplicate triples added
    added_again = sum(
        store.add_triples(t, source="rerun") for t in results.values() if t
    )
    assert added_again == 0
