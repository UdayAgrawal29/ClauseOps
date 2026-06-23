"""
Tests for M2 task-generation logic (Phase C + E):
  - deadline<->obligation locality (no cross-obligation bleed)
  - obligation dedup keeps distinct actions sharing a verb
  - modality-driven titles: no dangling prepositions, abstention -> review,
    prohibition phrasing, party hygiene
  - quality gate is a subset (precision monotonicity)

Pure-function tests (no models / no PDF).

Run:
  venv\\Scripts\\python.exe -m pytest tests/obligation_detection/test_task_generation_m2.py -q
"""

from __future__ import annotations

from datetime import date

from hypothesis import given, settings
from hypothesis import strategies as st

from clauseops.obligation_detection.deontic_classifier import ObligationRecord
from clauseops.obligation_detection.date_normalizer import DeadlineRecord
from clauseops.obligation_detection import task_generator as tg
from clauseops.obligation_detection.config import TaskGenerationConfig


def _obl(party, action, otype="OBLIGATION", conf=0.9, agent_score=15.0):
    return ObligationRecord(
        clause_id="c", obligation_type=otype, obligated_party=party,
        action_verb=action.split()[0] if action else "perform",
        beneficiary=None, modal_trigger="shall", confidence=conf,
        action=action, agent_score=agent_score, action_score=12.0,
    )


def _dl(raw, dtype="RELATIVE", norm=None, review=False, rec=None):
    return DeadlineRecord(
        clause_id="c", raw_text=raw, date_type=dtype, normalized_date=norm,
        anchor_date=date(2024, 1, 1), requires_review=review,
        deadline_label="X", recurrence_description=rec,
    )


# ── Deadline locality: each obligation gets the deadline in its own sentence ──
def test_deadline_locality_no_bleed():
    body = ("Party A shall deliver the goods within ten (10) days. "
            "Party B shall pay the invoice within thirty (30) days.")
    d10 = _dl("ten (10) days")
    d30 = _dl("thirty (30) days")
    a = tg._associate_deadline(_obl("Party A", "deliver the goods"), [d10, d30], body)
    b = tg._associate_deadline(_obl("Party B", "pay the invoice"), [d10, d30], body)
    assert a is d10
    assert b is d30


def test_deadline_none_when_not_in_unit():
    body = "Party A shall maintain confidentiality of all information disclosed hereunder."
    # A deadline that lives far away / not in this body shouldn't attach.
    far = _dl("ninety (90) days")
    assert tg._associate_deadline(_obl("Party A", "maintain confidentiality"), [far], body) is None


def test_recurring_deadline_attaches_in_sentence():
    body = ("On or before the tenth day of each calendar month, the Vendor shall "
            "prepare a report detailing revenues.")
    rec = _dl("the tenth day of each calendar month", dtype="RECURRING",
              rec="the tenth day of each calendar month")
    got = tg._associate_deadline(_obl("the Vendor", "prepare a report detailing revenues"),
                                 [rec], body)
    assert got is rec


# ── Dedup keeps distinct actions that share a verb ───────────────────────────
def test_dedup_keeps_distinct_actions_same_verb():
    obls = [
        _obl("DeltaThree", "reimburse PrimeCall for printing costs"),
        _obl("DeltaThree", "reimburse PrimeCall for customer-service costs"),
        _obl("DeltaThree", "reimburse PrimeCall for printing costs"),  # exact dup
    ]
    out = tg._dedupe_obligations(obls)
    assert len(out) == 2


# ── Title hygiene ────────────────────────────────────────────────────────────
def test_title_no_dangling_and_modality():
    # Obligation with no deadline must not end on a preposition.
    t = tg._format_title("PAYMENT", _obl("PrimeCall", "negotiate and contract for printing"), None)
    assert not t.rstrip().endswith(("by", "within", "for", "to", "of"))
    assert t.startswith("PrimeCall shall")

    p = tg._format_title("ASSIGNMENT",
                         _obl("Neither Party", "transfer or assign its rights", otype="PROHIBITION"),
                         None)
    assert "must not" in p


def test_title_abstention_routes_to_review_text():
    obl = _obl("the Members", "", otype="OBLIGATION")  # action abstained
    obl.action_verb = "perform"
    title = tg._format_title("GENERAL", obl, None)
    assert title.lower().startswith("review:")
    assert "perform" not in title.lower()


def test_clean_party_strips_artifacts():
    assert tg._clean_party('  "the Company".  ') == "the Company"
    assert tg._clean_party("[Licensee]") == "Licensee"


def test_is_plausible_party():
    assert tg._is_plausible_party("the Company")
    assert tg._is_plausible_party("Neither Party")
    # Clause fragments masquerading as parties → implausible.
    assert not tg._is_plausible_party("Chase's system or if the customer accesses the Chase")
    assert not tg._is_plausible_party("X shall do this and the other party will respond promptly soon")


def test_recurring_title_uses_recurrence():
    rec = _dl("the tenth day of each calendar month", dtype="RECURRING",
              rec="the tenth day of each calendar month")
    title = tg._format_title("REPORTING_AUDIT",
                             _obl("the Vendor", "prepare a monthly report"), rec)
    assert "each calendar month" in title


# ── Property: associated deadline (if any) shares the obligation's sentence ──
_word = st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu")), min_size=2, max_size=8)


@settings(max_examples=150)
@given(
    act1=st.lists(_word, min_size=1, max_size=4),
    act2=st.lists(_word, min_size=1, max_size=4),
    n1=st.integers(min_value=1, max_value=99),
    n2=st.integers(min_value=1, max_value=99),
)
def test_property_deadline_in_same_sentence(act1, act2, n1, n2):
    a1 = "do " + " ".join(act1)
    a2 = "make " + " ".join(act2)
    body = (f"The first party shall {a1} within {n1} days. "
            f"The second party shall {a2} within {n2} days.")
    d1 = _dl(f"{n1} days")
    d2 = _dl(f"{n2} days")
    got = tg._associate_deadline(_obl("The first party", a1), [d1, d2], body)
    if got is not None:
        sent = tg._sentence_window(body, body.find(a1))
        assert got.raw_text in sent


# ── Quality gate monotonicity is exercised at the contract level elsewhere; ──
# here we sanity-check config presets carry the new fields.
def test_config_presets_have_quality_fields():
    c = TaskGenerationConfig()
    assert hasattr(c, "min_agent_score") and hasattr(c, "review_low_quality")
    assert c.min_confidence == 0.55


def test_dedupe_tasks_keeps_distinct_same_clause_obligations():
    # M3 regression: 5 distinct NVOS obligations in ONE clause must NOT collapse.
    def mk(title):
        return tg.TaskRecord(
            task_id="x", contract_name="c", clause_id="clause_14", clause_type="PAYMENT",
            title=title, description="", obligated_party="NVOS", beneficiary=None,
            obligation_type="OBLIGATION", due_date=None, date_type="NONE",
            priority="MEDIUM", requires_review=False,
        )
    tasks = [
        mk("NVOS shall maintain all financial records"),
        mk("NVOS shall assign and direct operational staff"),
        mk("NVOS shall remunerate HGF thirty percent of net income"),
        mk("NVOS shall purchase product at cost plus five percent"),
        mk("NVOS shall maintain all financial records"),  # exact dup
    ]
    out = tg._dedupe_tasks(tasks)
    assert len(out) == 4  # 4 distinct, 1 dup removed
