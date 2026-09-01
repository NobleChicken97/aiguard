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


class WebSearchTool(Tool):
    def get_name(self):
        return "web_search"

    def get_description(self):
        return "Search the web for information. Returns a list of results with title, URL, and snippet."

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
