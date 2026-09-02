from abc import ABC, abstractmethod


class ToolResult:
    def __init__(self, status, output, guardrail_verdict=None, approval_reason=None):
        self.status = status
        self.output = output
        self.guardrail_verdict = guardrail_verdict
        self.approval_reason = approval_reason

    def to_dict(self):
        return {
            "status": self.status,
            "output": self.output,
            "guardrail_verdict": self.guardrail_verdict,
            "approval_reason": self.approval_reason,
        }


class Tool(ABC):
    def __init__(self):
        self.name = self.get_name()
        self.description = self.get_description()

    @abstractmethod
    def get_name(self):
        ...


    @abstractmethod
    def get_description(self):
        ...


    @abstractmethod
    def get_input_schema(self):
        ...


    @abstractmethod
    def execute(self, **kwargs):
        ...


    def to_schema(self):
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.get_input_schema(),
        }


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, tool):
        if not isinstance(tool, Tool):
            raise TypeError(f"Expected Tool instance, got {type(tool)}")
        self._tools[tool.name] = tool

    def get(self, name):
        return self._tools.get(name)

    def get_schemas(self):
        return [tool.to_schema() for tool in self._tools.values()]


def execute_with_retry(tool, kwargs, call_id, trace=None):
    """Execute a tool with bounded retries and exponential backoff.

    Shared by ``Orchestrator._retry_execute`` and the supervisor/worker path
    so resilience behavior is identical on every execution route.
    """
    import time

    import config

    last_result = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            result = tool.execute(**kwargs)
            if result.status in ("blocked", "denied"):
                return result
            if result.status == "success":
                return result
            if result.status == "failed" and attempt < config.MAX_RETRIES:
                if trace:
                    trace.log_retry(call_id, attempt, result.output)
                backoff = config.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                time.sleep(backoff)
                last_result = result
                continue
            return result
        except Exception as e:
            if attempt < config.MAX_RETRIES:
                if trace:
                    trace.log_retry(call_id, attempt, str(e))
                backoff = config.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                time.sleep(backoff)
                last_result = ToolResult(
                    status="failed",
                    output=f"Transient error (attempt {attempt}): {e}",
                )
                continue
            if trace:
                trace.log_error(f"Tool execution failed after {attempt} attempts: {e}")
            return ToolResult(
                status="failed",
                output=f"Error after {attempt} attempts: {e}",
            )

    return last_result or ToolResult(status="failed", output="Unknown failure.")
