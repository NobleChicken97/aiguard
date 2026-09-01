import sqlglot
from sqlglot import exp
from dataclasses import dataclass
from typing import Optional
import config


VERDICT_BLOCKED = "BLOCKED"
VERDICT_REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
VERDICT_ALLOWED = "ALLOWED"

BLOCK_STATEMENT_TYPES = {
    exp.Drop: "DROP",
    exp.TruncateTable: "TRUNCATE",
    exp.Alter: "ALTER",
    exp.Create: "CREATE",
}


@dataclass
class GuardrailResult:
    verdict: str
    reason: str = ""
    statement_type: str = ""
    tables: list = None

    def __post_init__(self):
        if self.tables is None:
            self.tables = []

    @property
    def blocked(self):
        return self.verdict == VERDICT_BLOCKED

    @property
    def requires_approval(self):
        return self.verdict == VERDICT_REQUIRES_APPROVAL

    @property
    def allowed(self):
        return self.verdict == VERDICT_ALLOWED

    def to_dict(self):
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "statement_type": self.statement_type,
            "tables": self.tables,
        }


class SQLGuardrail:
    """Statically parses SQL via sqlglot AST and enforces a rule set.

    BLOCK outright:
      - DROP, TRUNCATE, ALTER, CREATE
      - DELETE without WHERE
      - UPDATE without WHERE
      - Any table not in the allow-list

    REQUIRE APPROVAL:
      - Multi-statement batches

    ALLOW automatically:
      - SELECT against allow-listed tables
      - INSERT against allow-listed tables
      - UPDATE/DELETE with WHERE (row-count check done by SQL tool)
    """

    def __init__(self, allowed_tables=None):
        self.allowed_tables = allowed_tables or config.ALLOWED_TABLES

    def check(self, sql):
        sql = sql.strip()
        if not sql:
            return GuardrailResult(
                verdict=VERDICT_BLOCKED,
                reason="Empty SQL statement.",
            )

        try:
            statements = sqlglot.parse(sql, read="sqlite")
        except Exception as e:
            return GuardrailResult(
                verdict=VERDICT_BLOCKED,
                reason=f"Unparseable SQL: {e}",
            )

        statements = [s for s in statements if s is not None]
        if len(statements) == 0:
            return GuardrailResult(
                verdict=VERDICT_BLOCKED,
                reason="No parseable SQL statement found.",
            )

        if len(statements) > 1:
            for s in statements:
                res = self._check_single(s)
                if res.verdict == VERDICT_BLOCKED:
                    return res
            types = [type(s).__name__ for s in statements]
            return GuardrailResult(
                verdict=VERDICT_REQUIRES_APPROVAL,
                reason=f"Multi-statement batch detected ({len(statements)} statements: {types}).",
                statement_type="multi_statement",
            )

        return self._check_single(statements[0])

    def _check_single(self, expression):
        tables = [t.name for t in expression.find_all(exp.Table)]
        tables_lower = [t.lower() for t in tables]

        for stmt_cls, label in BLOCK_STATEMENT_TYPES.items():
            if isinstance(expression, stmt_cls):
                return GuardrailResult(
                    verdict=VERDICT_BLOCKED,
                    reason=f"{label} statements are not allowed.",
                    statement_type=label,
                    tables=tables,
                )

        if isinstance(expression, exp.Delete):
            if expression.args.get("where") is None:
                return GuardrailResult(
                    verdict=VERDICT_BLOCKED,
                    reason="DELETE without a WHERE clause is not allowed.",
                    statement_type="DELETE",
                    tables=tables,
                )
            table_check = self._check_tables(tables_lower)
            if table_check:
                return table_check
            return GuardrailResult(
                verdict=VERDICT_ALLOWED,
                statement_type="DELETE",
                tables=tables,
            )

        if isinstance(expression, exp.Update):
            if expression.args.get("where") is None:
                return GuardrailResult(
                    verdict=VERDICT_BLOCKED,
                    reason="UPDATE without a WHERE clause is not allowed.",
                    statement_type="UPDATE",
                    tables=tables,
                )
            table_check = self._check_tables(tables_lower)
            if table_check:
                return table_check
            return GuardrailResult(
                verdict=VERDICT_ALLOWED,
                statement_type="UPDATE",
                tables=tables,
            )

        if isinstance(expression, (exp.Select, exp.Insert)):
            table_check = self._check_tables(tables_lower)
            if table_check:
                return table_check
            stmt_type = "SELECT" if isinstance(expression, exp.Select) else "INSERT"
            return GuardrailResult(
                verdict=VERDICT_ALLOWED,
                statement_type=stmt_type,
                tables=tables,
            )

        return GuardrailResult(
            verdict=VERDICT_BLOCKED,
            reason=f"Statement type {type(expression).__name__} is not explicitly allowed.",
            statement_type=type(expression).__name__,
            tables=tables,
        )

    def _check_tables(self, tables_lower):
        for t in tables_lower:
            if t not in self.allowed_tables:
                return GuardrailResult(
                    verdict=VERDICT_BLOCKED,
                    reason=f"Table '{t}' is not in the allow-list: {sorted(self.allowed_tables)}.",
                    statement_type="unknown",
                    tables=tables_lower,
                )
        return None
