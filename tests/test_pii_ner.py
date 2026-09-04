"""NER second pass for PII masking (Phase 2, en_core_web_sm 3.8.0, pinned).

Measured on the demo seed (see STATUS.md Phase 2 for the full table):
- PERSON: 10/10 customer names, 0/8 cities, 2/10 product names (FP)
- GPE:    8/8 cities, 0 elsewhere
- ORG:    0 signal on seed data (kept: harmless, future-proof)
- Robustness variants (caps/spacing/initials/hyphen): 7/7 masked.
  Known slip (documented, not asserted): an isolated uncommon name with
  no context ("Henrietta Lacks") is missed — the regex layer and the
  column policy are the backstops for that shape.
"""

import sys

import pytest

sys.path.insert(0, ".")

import config
from db.database import reset_db
from db.seed import CUSTOMERS, seed_demo_data
from db.seed import PRODUCTS
from guardrails.pii_guardrail import PIIGuardrail
from tools.sql_tool import SQLTool


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


NAMES = [c[0] for c in CUSTOMERS]
CITIES = sorted({c[2] for c in CUSTOMERS})
PRODUCTS_NAMES = [p[0] for p in PRODUCTS]


def test_default_entity_set():
    assert PIIGuardrail.NER_ENTITIES == ("PERSON", "GPE", "ORG")


def test_person_recall_on_seed_names():
    masked = sum(1 for n in NAMES if PIIGuardrail.mask_pii_ner(n) != n)
    assert masked == 10 == len(NAMES)


def test_gpe_recall_on_seed_cities():
    masked = sum(1 for c in CITIES if PIIGuardrail.mask_pii_ner(c) != c)
    assert masked == 8 == len(CITIES)


def test_measured_product_name_false_positives():
    # Locked to the pinned model: exactly Wireless Mouse + Desk Lamp read
    # as PERSON. If a model upgrade changes this, update the count AND the
    # STATUS.md table together.
    masked = [p for p in PRODUCTS_NAMES if PIIGuardrail.mask_pii_ner(p) != p]
    assert sorted(masked) == ["Desk Lamp", "Wireless Mouse"]


def test_obfuscation_variants_still_masked():
    for variant in [
        "ALICE JOHNSON",
        "Alice  Johnson",
        "Alice J.",
        "A. Johnson",
        "Bob Smith-Jones",
        "alice johnson",
        "Dr. Carol White",
    ]:
        assert PIIGuardrail.mask_pii_ner(variant) != variant, variant


def test_entity_subset_parameter():
    # ORG-only pass leaves a person name alone.
    assert PIIGuardrail.mask_pii_ner("Alice Johnson", entities=("ORG",)) == "Alice Johnson"
    assert PIIGuardrail.mask_pii_ner("Alice Johnson", entities=("PERSON",)) == "***"


def test_non_string_input_coerced():
    assert PIIGuardrail.mask_pii_ner(12345) == "12345"


def test_missing_model_fails_safe_to_regex_layer(monkeypatch):
    def _boom(cls):
        raise RuntimeError("no model here")

    monkeypatch.setattr(PIIGuardrail, "_ner_model", classmethod(_boom))
    assert PIIGuardrail.mask_pii_ner("Alice Johnson") == "Alice Johnson"


def test_ner_off_by_default_in_sql_output():
    tool = SQLTool()
    res = tool.execute("SELECT name FROM customers WHERE id = 1", _call_id="ner-off")
    assert res.status == "success"
    assert "Alice Johnson" in res.output


def test_ner_on_masks_names_in_sql_output(monkeypatch):
    monkeypatch.setattr(config, "PII_NER_ENABLED", True)
    tool = SQLTool()
    res = tool.execute("SELECT name FROM customers WHERE id = 1", _call_id="ner-on")
    assert res.status == "success"
    assert "Alice Johnson" not in res.output
    assert "***" in res.output
