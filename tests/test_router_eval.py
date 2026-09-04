"""Router eval metric tests (hermetic — no provider key needed).

Scripted oracle clients stand in for the live model so the metric math
(accuracy over decided, deferral accounting, miss listing) is pinned;
the live run itself is scripts/router_eval.py with a configured key.
"""

import json
import sys

sys.path.insert(0, ".")

from agent.llm_client import ContentBlock, LLMResponse
from scripts.router_eval import CASES, EvalCase, evaluate, summarize

assert len(CASES) == 40, "eval set must stay at 40 cases (20 SQL + 20 research)"


def _oracle_factory(route_fn, confidence=0.95):
    """Client factory answering each case's router call with canned JSON."""

    def factory(case):
        route, conf = route_fn(case)

        class _Oracle:
            def call(self, system, messages, tools=None):
                return LLMResponse(
                    stop_reason="end_turn",
                    content=[
                        ContentBlock(
                            type="text",
                            text=json.dumps({
                                "route": route,
                                "confidence": conf,
                                "reasoning": "oracle",
                            }),
                        )
                    ],
                )

        return _Oracle()

    return factory


def _perfect(case):
    return ("SQL" if case.expected == "SQLWorker" else "RESEARCH"), 0.95


def test_perfect_oracle_scores_full_accuracy():
    results = evaluate(_oracle_factory(_perfect))
    summary = summarize(results)
    assert summary == {
        "total": 40,
        "decided": 40,
        "deferred": 0,
        "correct": 40,
        "accuracy": 1.0,
        "misses": [],
    }


def test_mixed_oracle_counts_misses():
    def mixed(case):
        idx = CASES.index(case)
        if idx % 2:
            flipped = "RESEARCH" if case.expected == "SQLWorker" else "SQL"
            return flipped, 0.9
        return _perfect(case)

    summary = summarize(evaluate(_oracle_factory(mixed)))
    assert summary["decided"] == 40
    assert summary["correct"] == 20
    assert summary["accuracy"] == 0.5
    assert len(summary["misses"]) == 20


def test_low_confidence_counts_as_deferred_not_wrong():
    def unsure(case):
        route, _ = _perfect(case)
        return route, 0.3

    summary = summarize(evaluate(_oracle_factory(unsure)))
    assert summary["decided"] == 0
    assert summary["deferred"] == 40
    assert summary["accuracy"] is None
    assert summary["misses"] == []


def test_garbage_replies_defer():
    def factory(case):
        class _Garbage:
            def call(self, system, messages, tools=None):
                return LLMResponse(
                    stop_reason="end_turn",
                    content=[ContentBlock(type="text", text="???")],
                )

        return _Garbage()

    summary = summarize(evaluate(factory))
    assert summary["deferred"] == 40
    assert summary["accuracy"] is None


def test_threshold_is_honored(monkeypatch):
    import config

    monkeypatch.setattr(config, "ROUTER_CONFIDENCE_THRESHOLD", 0.99)
    summary = summarize(evaluate(_oracle_factory(_perfect, confidence=0.95)))
    assert summary["deferred"] == 40  # 0.95 < raised bar


def test_eval_set_shape():
    sql = [c for c in CASES if c.expected == "SQLWorker"]
    research = [c for c in CASES if c.expected == "ResearchWorker"]
    assert len(sql) == 20 and len(research) == 20
    assert len({c.prompt for c in CASES}) == 40  # no duplicates
    assert all(isinstance(c, EvalCase) for c in CASES)
