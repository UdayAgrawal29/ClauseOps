"""
Tests for M4 (B1 duty-table mining + B3 passive operational obligations).

Pure-function tests (no models / no PDF).

Run:
  venv\\Scripts\\python.exe -m pytest tests/obligation_detection/test_table_and_passive.py -q
"""

from __future__ import annotations

from clauseops.obligation_detection import deontic_classifier as dc


# ── B1: duty TABLE mining ────────────────────────────────────────────────────
def test_duty_table_mined_to_obligations():
    md = (
        "| Member | Duties Description |\n"
        "| --- | --- |\n"
        "| BorrowMoney.com, inc | HTML code, build, deploy and maintain all technical requirements |\n"
        "| JVLS, LLC dba Vaccines 2Go | secure monthly government and private awarded contracts |\n"
    )
    cands = dc._table_obligation_candidates(md, ["BorrowMoney.com, inc", "JVLS, LLC dba Vaccines 2Go"])
    assert len(cands) == 2
    parties = {c[1] for c in cands}
    assert "BorrowMoney.com, inc" in parties
    # reconstructed "<party> shall <duty>", grounded duty text, not review
    assert all(c[0].lower().startswith(c[1].lower() + " shall ") for c in cands)
    assert all(c[3] is False for c in cands)


def test_non_duty_table_ignored():
    # A royalty/payment schedule must NOT be mined into obligations.
    md = (
        "| Subscribers | Royalty Payable as Percentage of Gross Revenue |\n"
        "| --- | --- |\n"
        "| 0 - 5000 | 6.25% |\n"
        "| 5001 - 7500 | 6.75% |\n"
    )
    assert dc._table_obligation_candidates(md, ["Licensee", "Licensor"]) == []


def test_table_row_without_real_party_skipped():
    md = (
        "| Item | Responsibilities |\n"
        "| --- | --- |\n"
        "| 0 - 5000 | do something generic and unattributed here |\n"
    )
    assert dc._table_obligation_candidates(md, ["Licensee"]) == []


def test_looks_like_markdown_table():
    assert dc._looks_like_markdown_table("| a | b |\n| c | d |")
    assert not dc._looks_like_markdown_table("just a normal sentence.")


# ── B3: passive operational obligations ──────────────────────────────────────
def test_passive_rewrite_with_known_org_party():
    raw = "All company records will be kept for a minimum of five (5) years."
    out = dc._passive_obligation_rewrite(raw, None, ["NVOS", "the Company"])
    assert out is not None
    recon, agent = out
    assert agent == "the Company"
    assert recon.startswith("the Company shall keep")


def test_passive_rewrite_uses_heading_agent_first():
    raw = "Quarterly reports will be prepared and circulated to the Members."
    out = dc._passive_obligation_rewrite(raw, "NVOS", ["NVOS", "the Company"])
    assert out is not None and out[1] == "NVOS"
    assert out[0].startswith("NVOS shall prepare")


def test_passive_rewrite_uses_in_sentence_entity():
    # "the Venture" is named in the sentence -> preferred grounded agent,
    # even when the per-clause party list is empty.
    raw = ("Accurate and complete books of account of the transactions of the Venture "
           "will be kept in accordance with GAAP.")
    out = dc._passive_obligation_rewrite(raw, None, [])
    assert out is not None
    recon, agent = out
    assert agent.lower() == "the venture"
    assert recon.lower().startswith("the venture shall keep")


def test_passive_rewrite_skips_without_real_party():
    # No org/collective party available -> do not invent one.
    raw = "All records will be kept for five years."
    assert dc._passive_obligation_rewrite(raw, None, ["John Smith", "Jane Doe"]) is None


def test_passive_rewrite_skips_non_operational_subject():
    # "this Agreement will be governed" is not an operational duty.
    raw = "This Agreement will be governed by the laws of Florida."
    assert dc._passive_obligation_rewrite(raw, "the Company", ["the Company"]) is None


def test_passive_rewrite_skips_non_action_participle():
    raw = "The provisions will be deemed severable and the rest unaffected."
    assert dc._passive_obligation_rewrite(raw, "the Company", ["the Company"]) is None
