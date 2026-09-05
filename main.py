import argparse
import sys

from agent.llm_client import FakeLLMClient
from agent.llm_client import build_llm_client as build_provider_client
from agent.orchestrator import Orchestrator
from approval.gate import AutoApproveHandler, AutoDenyHandler, CLIApprovalHandler
from db.database import initialize_db
from db.seed import seed_demo_data

NO_KEY_HINT = (
    "No LLM API key configured. Set LLM_PROVIDER (gemini, groq, nvidia, openai"
    " or openai-compat) with LLM_API_KEY — or ANTHROPIC_API_KEY for the Claude"
    " path. Use --demo for a local dry run."
)


def build_approval_handler(mode):
    if mode == "cli":
        return CLIApprovalHandler()
    if mode == "auto-approve":
        return AutoApproveHandler()
    if mode == "auto-deny":
        return AutoDenyHandler()
    raise ValueError(f"Unsupported approval mode: {mode}")


def build_llm_client(demo=False):
    if demo:
        return FakeLLMClient([
            FakeLLMClient.tool_use_response("calculator", {"expression": "15 * 37"}, "demo_toolu_1"),
            FakeLLMClient.text_response("15 * 37 = 555."),
        ])

    client = build_provider_client()
    if client is None:
        raise RuntimeError(NO_KEY_HINT)
    return client


def prepare_database(seed=False):
    initialize_db()
    if seed:
        seed_demo_data()


def run_prompt(prompt, user_id, approval_mode, demo=False):
    prepare_database(seed=True)
    llm_client = build_llm_client(demo=demo)
    orchestrator = Orchestrator(
        llm_client=llm_client,
        approval_handler=build_approval_handler(approval_mode),
        user_id=user_id,
    )
    result = orchestrator.run(prompt)
    print(result)
    return 0


def run_interactive(user_id, approval_mode):
    prepare_database(seed=True)
    llm_client = build_llm_client(demo=False)
    orchestrator = Orchestrator(
        llm_client=llm_client,
        approval_handler=build_approval_handler(approval_mode),
        user_id=user_id,
    )

    print("Interactive mode. Type 'exit' or 'quit' to stop.")
    while True:
        try:
            prompt = input("You> ").strip()
        except EOFError:
            print()
            break
        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit"}:
            break
        print(f"Assistant> {orchestrator.run(prompt)}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the AiGuard demo.")
    parser.add_argument("--user-id", default="default", help="User id to associate with the session.")
    parser.add_argument(
        "--approval-mode",
        choices=("cli", "auto-approve", "auto-deny"),
        default="cli",
        help="How approval-gated actions should be handled.",
    )
    parser.add_argument("--prompt", help="Run a single prompt and exit.")
    parser.add_argument("--demo", action="store_true", help="Run without a Claude key using a scripted demo response.")
    args = parser.parse_args(argv)

    if args.prompt:
        return run_prompt(args.prompt, args.user_id, args.approval_mode, demo=args.demo)

    if args.demo:
        return run_prompt("What is 15 times 37?", args.user_id, args.approval_mode, demo=True)

    if build_provider_client() is None:
        print(NO_KEY_HINT, file=sys.stderr)
        return 1

    return run_interactive(args.user_id, args.approval_mode)


if __name__ == "__main__":
    raise SystemExit(main())