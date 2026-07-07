from pathlib import Path

from nsai.code2kb import extract_from_path
from nsai.code2kb.rust import extract, module_name
from nsai.kb import KnowledgeBase

RUST_SOURCE = """\
use std::collections::HashMap;
use crate::utils::helper;
use serde::{Serialize, Deserialize as De};

trait Animal {
    fn speak(&self) -> String;
}

struct Dog {
    name: String,
}

impl Dog {
    fn new(name: String) -> Dog {
        Dog { name }
    }

    fn bark(&self) -> String {
        String::from("woof")
    }
}

impl Animal for Dog {
    fn speak(&self) -> String {
        self.bark()
    }
}

fn adopt() -> Dog {
    helper();
    println!("adopting");
    Dog::new(String::from("rex"))
}
"""


def _write_crate(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "animal.rs").write_text(RUST_SOURCE)
    return src


def test_module_name_is_root_relative_dotted_path(tmp_path: Path):
    src = _write_crate(tmp_path)
    assert module_name(src / "animal.rs", tmp_path) == "src.animal"
    assert module_name(src / "animal.rs", src) == "animal"


def test_extract_structural_triples():
    triples = extract(RUST_SOURCE, "src.animal")

    assert triples[0] == ("src.animal", "rdf:type", "Module")
    # use declarations, :: → . ; crate/scoped-list/as forms all expanded
    assert ("src.animal", "imports", "std.collections.HashMap") in triples
    assert ("src.animal", "imports", "crate.utils.helper") in triples
    assert ("src.animal", "imports", "serde.Serialize") in triples
    # `as` rename records the source path, not the alias
    assert ("src.animal", "imports", "serde.Deserialize") in triples
    assert not any("De" == o for _, _, o in triples)

    # trait and struct are both Classes
    assert ("src.animal.Animal", "rdf:type", "Class") in triples
    assert ("src.animal.Dog", "rdf:type", "Class") in triples
    assert ("src.animal.Dog", "defined_in", "src.animal") in triples
    # `impl Animal for Dog` resolved against the same-file trait
    assert ("src.animal.Dog", "rdfs:subClassOf", "src.animal.Animal") in triples

    # impl methods hang off the SelfType; free functions off the module
    assert ("src.animal.Dog.new", "rdf:type", "Function") in triples
    assert ("src.animal.Dog.speak", "defined_in", "src.animal") in triples
    assert ("src.animal.adopt", "rdf:type", "Function") in triples

    # self.bark() resolves to the enclosing impl's SelfType
    assert ("src.animal.Dog.speak", "calls", "src.animal.Dog.bark") in triples
    # Dog::new resolved to the local struct; helper() through the use binding
    assert ("src.animal.adopt", "calls", "src.animal.Dog.new") in triples
    assert ("src.animal.adopt", "calls", "crate.utils.helper") in triples
    # unresolvable external path recorded verbatim
    assert ("src.animal.Dog.bark", "calls", "String.from") in triples

    # macros are skipped, and Rust has no `raises`
    assert not any("println" in t for triple in triples for t in triple)
    assert not any(p == "raises" for _, p, _ in triples)


def test_inline_mods_wildcards_and_bad_syntax():
    triples = extract(
        "use x::y::*;\n"
        "use a::b::{c, d as e};\n"
        "mod inner {\n"
        "    pub enum Color { Red }\n"
        "    pub fn nested() {}\n"
        "}\n"
        "union U { a: u32 }\n"
        "impl U {\n"
        "    fn zero() -> U { U { a: 0 } }\n"
        "}\n"
        "fn top() { e(); }\n",
        "lib",
    )
    assert ("lib", "imports", "x.y") in triples  # wildcard → prefix only
    assert ("lib", "imports", "a.b.c") in triples
    assert ("lib", "imports", "a.b.d") in triples
    # inline mod adds a qualification segment
    assert ("lib.inner.Color", "rdf:type", "Class") in triples
    assert ("lib.inner.nested", "rdf:type", "Function") in triples
    assert ("lib.U", "rdf:type", "Class") in triples
    assert ("lib.U.zero", "rdf:type", "Function") in triples
    # inherent impl emits no subClassOf
    assert not any(p == "rdfs:subClassOf" for _, p, _ in triples)
    # aliased call resolves back to the use source path
    assert ("lib.top", "calls", "a.b.d") in triples

    # tree-sitter degrades instead of raising on bad syntax
    broken = extract("fn oops(:\n", "bad")
    assert broken[0] == ("bad", "rdf:type", "Module")


def test_extract_from_path_skips_target_and_feeds_kb(tmp_path: Path):
    src = _write_crate(tmp_path)
    decoy = tmp_path / "target" / "debug"
    decoy.mkdir(parents=True)
    (decoy / "vendored.rs").write_text("fn hidden() {}\n")

    results = dict(extract_from_path(tmp_path))
    assert [p.name for p in results] == ["animal.rs"]  # target/ skipped
    triples = results[src / "animal.rs"]
    assert ("src.animal", "rdf:type", "Module") in triples

    # names survive the KB round trip and carry provenance
    store = KnowledgeBase(tmp_path / "kb.ttl")
    store.add_triples(triples, source="src/animal.rs (static-analysis)")
    assert ("ns:src.animal.Dog", "ns:defined_in", "ns:src.animal") in store.find(
        predicate="ns:defined_in"
    )
    assert ("ns:src.animal.Dog", "rdfs:subClassOf", "ns:src.animal.Animal") in store.find(
        predicate="rdfs:subClassOf"
    )
