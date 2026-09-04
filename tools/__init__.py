"""Tool registry, base class, and built-in tools.

The package is consumed as ``from tools import ToolRegistry, SQLTool, ...``
through the explicit ``__all__`` below; the underlying modules can be
imported directly when you need lower-level access (e.g. ``tools.base``).
"""

from tools.base import Tool, ToolResult, ToolRegistry, execute_with_retry
from tools.calculator import CalculatorTool, safe_eval
from tools.query_builder import (
    FilterCondition,
    QueryBuilderError,
    QueryBuilderRequest,
    build_select_sql,
    get_builder_schema,
    run_builder_query,
)
from tools.sql_tool import SQLTool
from tools.web_search import MockWebSearchTool

__all__ = [
    # Base
    "Tool",
    "ToolResult",
    "ToolRegistry",
    "execute_with_retry",
    # Built-in tools
    "CalculatorTool",
    "safe_eval",
    "SQLTool",
    "MockWebSearchTool",
    # Query builder
    "FilterCondition",
    "QueryBuilderError",
    "QueryBuilderRequest",
    "build_select_sql",
    "get_builder_schema",
    "run_builder_query",
]
