"""Canned demo search stub — explicitly a mock (Phase 5), not a roadmap item.

``MockWebSearchTool`` returns fixed results for two demo queries and
generic placeholder results otherwise. Keeping a stub is INTENTIONAL
scope-limiting for this project (its safety story is the database path),
and it is labeled as a mock in the class name, the tool name, the
LLM-facing description, the chat UI, and the README — no code path may
present its output as live research.

Future live-integration seam (when a provider key exists): replace this
class with an httpx client that POSTs ``{"query": ...}`` to Tavily
(``SEARCH_API_URL=https://api.tavily.com/search`` with
``SEARCH_API_KEY``) or Brave, caps results per call, enforces a monthly
spend guard next to the session budgets, and keeps this canned class
behind a ``SEARCH_API_KEY``-unset fallback for offline demos/tests.
"""

from tools.base import Tool, ToolResult


_MOCK_RESULTS = [
    {
        "query": "Python",
        "results": [
            {"title": "Welcome to Python.org", "url": "https://www.python.org", "snippet": "The official home of the Python programming language."},
            {"title": "Python (programming language) - Wikipedia", "url": "https://en.wikipedia.org/wiki/Python_(programming_language)", "snippet": "Python is a high-level, general-purpose programming language."},
        ],
    },
    {
        "query": "FastAPI",
        "results": [
            {"title": "FastAPI", "url": "https://fastapi.tiangolo.com", "snippet": "FastAPI is a modern, fast web framework for building APIs with Python."},
            {"title": "FastAPI Documentation", "url": "https://fastapi.tiangolo.com/tutorial/", "snippet": "Learn how to use FastAPI step by step."},
        ],
    },
    {
        "query": "default",
        "results": [
            {"title": "Search Result 1", "url": "https://example.com/1", "snippet": "This is a mock search result for demonstration purposes."},
            {"title": "Search Result 2", "url": "https://example.com/2", "snippet": "Another mock search result. Replace with a real search API for production use."},
        ],
    },
]


class MockWebSearchTool(Tool):
    """Demo search stub: deterministic canned results, never live data."""

    def get_name(self):
        return "mock_web_search"

    def get_description(self):
        return (
            "DEMO STUB — canned results, not live web data. Never claim to "
            "have browsed the web. Returns a list of canned results with "
            "title, URL, and snippet."
        )

    def get_input_schema(self):
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query string.",
                }
            },
            "required": ["query"],
        }

    def execute(self, query=None, **kwargs):
        if not query:
            return ToolResult(
                status="failed",
                output="Error: 'query' parameter is required.",
            )

        query_lower = query.lower()
        matched = None
        for entry in _MOCK_RESULTS:
            if entry["query"] == "default":
                continue
            if entry["query"].lower() in query_lower:
                matched = entry
                break

        if matched is None:
            matched = next(e for e in _MOCK_RESULTS if e["query"] == "default")

        results = matched["results"]
        formatted = "\n".join(
            f"[{i+1}] {r['title']}\n    {r['url']}\n    {r['snippet']}"
            for i, r in enumerate(results)
        )
        return ToolResult(
            status="success",
            output=f"Search results for '{query}':\n{formatted}",
        )
