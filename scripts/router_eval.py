"""Router eval (Phase 4): labeled accuracy for the SQL-vs-RESEARCH router.

Runs a fixed set of 40 hand-written prompts (20 SQL, 20 research — crisp
intent, no trick cases) through ``SupervisorAgent.route`` on any configured
provider and reports accuracy over decided cases plus the deferral count.
Deferrals (low-confidence → clarification question) are reported
separately: asking is correct behavior, not an error.

Usage:
    python -m scripts.router_eval            # live provider (needs a key)
    python -m scripts.router_eval --bar 0.9  # custom pass bar

Exit codes: 0 = accuracy over decided cases >= bar (or clean skip without
a key), 1 = below bar or errors. Hermetic coverage of the metric math
lives in tests/test_router_eval.py (no key needed).
"""

import argparse
import time
from dataclasses import dataclass

import config
from agent.llm_client import build_llm_client
from agent.supervisor import SupervisorAgent

PASS_BAR = 0.80


@dataclass
class EvalCase:
    prompt: str
    expected: str  # "SQLWorker" | "ResearchWorker"


@dataclass
class EvalResult:
    prompt: str
    expected: str
    worker: str
    confidence: float
    tier: str
    deferred: bool
    correct: bool
    seconds: float = 0.0


CASES = [
    # --- SQL (database entities: customers/products/orders/order_items) ---
    EvalCase("How many customers are in the database?", "SQLWorker"),
    EvalCase("List all products under $50.", "SQLWorker"),
    EvalCase("Who lives in Chicago?", "SQLWorker"),
    EvalCase("What is the total revenue from delivered orders?", "SQLWorker"),
    EvalCase("Show me the five most expensive products.", "SQLWorker"),
    EvalCase("Which customer placed the most orders?", "SQLWorker"),
    EvalCase("How many items are in order 9?", "SQLWorker"),
    EvalCase("List orders placed in March 2024.", "SQLWorker"),
    EvalCase("What is the average order total?", "SQLWorker"),
    EvalCase("Which products are low on stock (under 20)?", "SQLWorker"),
    EvalCase("Show all customers from New York and their signup dates.", "SQLWorker"),
    EvalCase("What categories of products do we sell?", "SQLWorker"),
    EvalCase("Find the order with the highest total.", "SQLWorker"),
    EvalCase("How many cancelled orders are there?", "SQLWorker"),
    EvalCase("List each customer's name alongside their city.", "SQLWorker"),
    EvalCase("What did customer Alice Johnson order?", "SQLWorker"),
    EvalCase("Show products in the Furniture category with prices.", "SQLWorker"),
    EvalCase("How many units of Wireless Mouse were ordered in total?", "SQLWorker"),
    EvalCase("Which orders are still pending?", "SQLWorker"),
    EvalCase("Count the customers by city.", "SQLWorker"),
    # --- RESEARCH (calculation / general knowledge, no DB entities) ---
    EvalCase("What is 15 times 37?", "ResearchWorker"),
    EvalCase("Who created the Python programming language?", "ResearchWorker"),
    EvalCase("What is the capital of France?", "ResearchWorker"),
    EvalCase("Calculate (15 + 27) * 3 / 9.", "ResearchWorker"),
    EvalCase("Tell me about the history of computing.", "ResearchWorker"),
    EvalCase("What is the square root of 144?", "ResearchWorker"),
    EvalCase("Search the web for FastAPI documentation.", "ResearchWorker"),
    EvalCase("How many ounces are in a gallon?", "ResearchWorker"),
    EvalCase("What is 15% of 240?", "ResearchWorker"),
    EvalCase("Who wrote the novel 1984?", "ResearchWorker"),
    EvalCase("Convert 100 US dollars to euros.", "ResearchWorker"),
    EvalCase("What year was the first iPhone released?", "ResearchWorker"),
    EvalCase("Compute 2 to the power of 16.", "ResearchWorker"),
    EvalCase("Summarize the plot of Hamlet.", "ResearchWorker"),
    EvalCase("What is the boiling point of water in Fahrenheit?", "ResearchWorker"),
    EvalCase("How far is the Earth from the Sun?", "ResearchWorker"),
    EvalCase("What is 7 factorial?", "ResearchWorker"),
    EvalCase("Look up the current price of Bitcoin.", "ResearchWorker"),
    EvalCase("Explain how photosynthesis works.", "ResearchWorker"),
    EvalCase("What is 100 divided by 7?", "ResearchWorker"),
]


def evaluate(client_factory, cases=None):
    """Route every case; never raises (per-case errors become deferred)."""
    results = []
    for case in cases or CASES:
        started = time.monotonic()
        try:
            supervisor = SupervisorAgent(llm_client=client_factory(case))
            decision = supervisor.route(case.prompt)
            deferred = decision.needs_clarification
            results.append(
                EvalResult(
                    prompt=case.prompt,
                    expected=case.expected,
                    worker=decision.worker,
                    confidence=decision.confidence,
                    tier=decision.tier,
                    deferred=deferred,
                    correct=(not deferred and decision.worker == case.expected),
                    seconds=time.monotonic() - started,
                )
            )
        except Exception:  # noqa: BLE001 — eval reports, never raises
            results.append(
                EvalResult(
                    prompt=case.prompt,
                    expected=case.expected,
                    worker="error",
                    confidence=0.0,
                    tier="error",
                    deferred=True,
                    correct=False,
                    seconds=time.monotonic() - started,
                )
            )
    return results


def summarize(results):
    decided = [r for r in results if not r.deferred]
    correct = [r for r in decided if r.correct]
    return {
        "total": len(results),
        "decided": len(decided),
        "deferred": len(results) - len(decided),
        "correct": len(correct),
        "accuracy": (len(correct) / len(decided)) if decided else None,
        "misses": [
            {"prompt": r.prompt, "expected": r.expected, "got": r.worker,
             "confidence": r.confidence, "tier": r.tier}
            for r in decided
            if not r.correct
        ],
    }


def _print_report(results, summary, provider, model, bar):
    print(f"\nNetSentry router eval — provider={provider} model={model} (40 cases)")
    print("=" * 68)
    for r in results:
        flag = "DEFER" if r.deferred else ("ok" if r.correct else "MISS")
        print(f"[{flag:5}] {r.expected:14} got={r.worker:14} "
              f"conf={r.confidence:.2f} {r.tier:10} {r.prompt[:44]}")
    print("=" * 68)
    acc = summary["accuracy"]
    acc_s = f"{acc:.1%}" if acc is not None else "n/a (all deferred)"
    print(f"accuracy over decided: {acc_s} "
          f"({summary['correct']}/{summary['decided']}), "
          f"deferred {summary['deferred']}/{summary['total']}, bar {bar:.0%}")
    for m in summary["misses"]:
        print(f"  MISS: {m['prompt']!r} expected {m['expected']}, got {m['got']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Router accuracy eval.")
    parser.add_argument("--bar", type=float, default=PASS_BAR)
    args = parser.parse_args(argv)

    provider = (config.LLM_PROVIDER or "anthropic").strip().lower()
    model = config.LLM_MODEL or ("claude (default)" if provider == "anthropic" else "provider default")

    client = build_llm_client()
    if client is None:
        print(
            "SKIPPED: no LLM key configured.\n"
            "Set LLM_PROVIDER (e.g. gemini or groq) and LLM_API_KEY in .env —\n"
            "see the 'LLM Provider' section of README.md. Metric math is covered\n"
            "without a key by tests/test_router_eval.py."
        )
        return 0

    results = evaluate(lambda _case: client)
    summary = summarize(results)
    _print_report(results, summary, provider, model, args.bar)
    if summary["accuracy"] is None or summary["accuracy"] < args.bar:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
