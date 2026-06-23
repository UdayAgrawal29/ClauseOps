"""
Property-based tests for the offline QA extractor's pure span decoder.

These exercise the runtime correctness invariants from the QA-extraction plan
WITHOUT requiring the trained model — `decode_answer` is a pure function over
logits + offsets, so hypothesis can drive it with arbitrary inputs.

  Property 1 — Grounding / extractiveness:
      any non-None answer is an exact substring of the context.
  Property 2 — Span validity:
      the decoded char span is within bounds and start <= end (enforced
      internally; we verify the externally observable consequence: the answer
      is a real slice of the context).
  Property 5 — Determinism:
      identical inputs produce identical outputs.

Run:
  venv\\Scripts\\python.exe -m pytest tests/obligation_detection/test_qa_extractor.py -q
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from clauseops.obligation_detection.qa_extractor import decode_answer


# A token layout: index 0 is CLS (sequence_id 0 / "question side"), the rest
# are context tokens. We build offset_mapping consistently with `context`.
def _build_inputs(context: str, n_ctx_tokens: int):
    """
    Build (offset_mapping, sequence_ids) for a CLS token + n_ctx_tokens context
    tokens that partition the context string into contiguous char spans.
    """
    offset_mapping = [None]          # CLS
    sequence_ids = [0]               # CLS belongs to the question side
    L = len(context)
    if n_ctx_tokens <= 0 or L == 0:
        # one trivial context token
        offset_mapping.append((0, L))
        sequence_ids.append(1)
        return offset_mapping, sequence_ids

    step = max(1, L // n_ctx_tokens)
    pos = 0
    while pos < L:
        end = min(L, pos + step)
        offset_mapping.append((pos, end))
        sequence_ids.append(1)
        pos = end
    return offset_mapping, sequence_ids


_logit = st.floats(min_value=-20, max_value=20, allow_nan=False, allow_infinity=False)


@settings(max_examples=400)
@given(
    context=st.text(min_size=1, max_size=120),
    n_ctx=st.integers(min_value=1, max_value=12),
    seed=st.integers(min_value=0, max_value=10_000),
    data=st.data(),
)
def test_decode_answer_is_grounded(context, n_ctx, seed, data):
    offset_mapping, sequence_ids = _build_inputs(context, n_ctx)
    n = len(offset_mapping)
    start_logits = data.draw(st.lists(_logit, min_size=n, max_size=n))
    end_logits = data.draw(st.lists(_logit, min_size=n, max_size=n))

    answer, score = decode_answer(
        context, offset_mapping, sequence_ids, start_logits, end_logits
    )
    assert isinstance(score, float)
    if answer is not None:
        # Property 1 + 2: the answer must be an exact substring of the context.
        assert answer in context


@settings(max_examples=200)
@given(
    context=st.text(min_size=1, max_size=120),
    n_ctx=st.integers(min_value=1, max_value=12),
    data=st.data(),
)
def test_decode_answer_is_deterministic(context, n_ctx, data):
    offset_mapping, sequence_ids = _build_inputs(context, n_ctx)
    n = len(offset_mapping)
    start_logits = data.draw(st.lists(_logit, min_size=n, max_size=n))
    end_logits = data.draw(st.lists(_logit, min_size=n, max_size=n))

    a1 = decode_answer(context, offset_mapping, sequence_ids, list(start_logits), list(end_logits))
    a2 = decode_answer(context, offset_mapping, sequence_ids, list(start_logits), list(end_logits))
    assert a1 == a2  # Property 5: determinism


def test_decode_answer_abstains_when_cls_dominates():
    # CLS (index 0) has a huge score → must abstain (no-answer).
    context = "The Company shall pay the fee."
    offset_mapping, sequence_ids = _build_inputs(context, 6)
    n = len(offset_mapping)
    start_logits = [0.0] * n
    end_logits = [0.0] * n
    start_logits[0] = 100.0
    end_logits[0] = 100.0
    answer, _ = decode_answer(context, offset_mapping, sequence_ids, start_logits, end_logits)
    assert answer is None


def test_decode_answer_returns_span_when_context_dominates():
    context = "The Company shall pay the fee."
    offset_mapping, sequence_ids = _build_inputs(context, 6)
    n = len(offset_mapping)
    start_logits = [0.0] * n
    end_logits = [0.0] * n
    # Favor a context token (index 1) over CLS.
    start_logits[1] = 50.0
    end_logits[1] = 50.0
    answer, _ = decode_answer(context, offset_mapping, sequence_ids, start_logits, end_logits)
    assert answer is not None
    assert answer in context
