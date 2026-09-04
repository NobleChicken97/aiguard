import sqlglot
from sqlglot import exp
from dataclasses import dataclass
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


# Query-shape anomaly thresholds (Phase 2). Log-only: crossing them emits a
# ``query_shape_anomaly`` trace event but never blocks — the foundation for
# future rate-based abuse detection, not an enforcement point.
SHAPE_TABLE_LIMIT = 2
SHAPE_COLUMN_LIMIT = 8


def get_allowed_schema_columns(allowed_tables=None):
    """Best-effort {table: [column, ...]} for the allow-listed tables.

    Used for SELECT * expansion. Returns {} on any error — an unknown
    schema only makes star-checks fail closed, never fail open.
    """
    from db.database import get_connection

    tables = sorted(allowed_tables or config.ALLOWED_TABLES)
    out = {}
    try:
        conn = get_connection()
        try:
            for table in tables:
                if config.DATABASE_URL.startswith("postgres"):
                    rows = conn.execute(
                        """SELECT column_name, data_type FROM information_schema.columns
                           WHERE table_name = ? ORDER BY ordinal_position""",
                        (table,),
                    ).fetchall()
                    cols = [row["column_name"] for row in rows]
                else:
                    cols = [
                        row["name"]
                        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                    ]
                if cols:
                    out[table.lower()] = [c.lower() for c in cols]
        finally:
            conn.close()
    except Exception:
        return {}
    return out


def _table_aliases(expression):
    """Map {alias -> real table} (both lower-cased) for the statement."""
    aliases = {}
    for table in expression.find_all(exp.Table):
        alias = (table.alias or "").lower()
        if alias and alias != table.name.lower():
            aliases[alias] = table.name.lower()
    return aliases


def _iter_columns(expression):
    """All referenced columns as ([(qualifier|None, name)], [star qualifiers]).

    Qualifiers resolve through aliases at check time. A bare ``*`` (or
    ``COUNT(*)``) yields a None star qualifier; ``t.*`` yields ``t``.
    """
    cols = []
    star_quals = set()
    for col in expression.find_all(exp.Column):
        qual = (col.table or "").lower() or None
        if isinstance(col.this, exp.Star):
            star_quals.add(qual)
        elif col.name:
            cols.append((qual, col.name.lower()))
    for star in expression.find_all(exp.Star):
        if not isinstance(getattr(star, "parent", None), exp.Column):
            star_quals.add(None)
    # INSERT column lists are Identifiers inside a Schema node, not Column
    # nodes — without this, INSERT INTO t (denied_col) would slip through.
    for schema in expression.find_all(exp.Schema):
        table = schema.this
        qual = (
            table.name.lower()
            if isinstance(table, exp.Table) and table.name
            else None
        )
        for ident in schema.expressions:
            if isinstance(ident, exp.Identifier) and ident.name:
                cols.append((qual, ident.name.lower()))
    return cols, sorted(star_quals, key=lambda q: (q is not None, q or ""))


def query_shape(sql):
    """Best-effort shape summary for anomaly logging. Never raises."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
        tables, col_set, star = set(), set(), False
        for statement in statements:
            tables.update(t.name.lower() for t in statement.find_all(exp.Table))
            cols, quals = _iter_columns(statement)
            col_set.update(cols)
            star = star or bool(quals)
        return {
            "tables": sorted(tables),
            "columns": len(col_set),
            "star": star,
            "statements": len(statements),
        }
    except Exception:
        return {"tables": [], "columns": 0, "star": False, "statements": 0}


def _insert_has_column_list(expression):
    """True when INSERT names its target columns (INSERT INTO t (a, b) ...)."""
    return expression.find(exp.Schema) is not None


def _insert_row_count(expression):
    """Number of VALUES tuples, or None for INSERT..SELECT / exotic forms."""
    inner = expression.args.get("expression")
    if isinstance(inner, exp.Values):
        return len(inner.expressions)
    return None


class SQLGuardrail:
    """Statically parses SQL via sqlglot AST and enforces a rule set.

    BLOCK outright:
      - DROP, TRUNCATE, ALTER, CREATE
      - DELETE without WHERE
      - UPDATE without WHERE
      - Any table not in the allow-list

    REQUIRE APPROVAL:
      - Multi-statement batches
      - INSERT batches above RISKY_ROW_THRESHOLD (or unknown row count)

    ALLOW automatically:
      - SELECT against allow-listed tables
      - Single-row INSERT against allow-listed tables
      - UPDATE/DELETE with WHERE (row-count check done by SQL tool)

    Column policy (COLUMN_POLICY): any statement referencing a denied
    column — SELECT, WHERE, JOIN, INSERT targets, UPDATE targets,
    including via SELECT * expansion — is BLOCKED outright.
    """

    def __init__(self, allowed_tables=None, column_policy=None, schema_columns=None):
        self.allowed_tables = allowed_tables or config.ALLOWED_TABLES
        policy = column_policy if column_policy is not None else config.COLUMN_POLICY
        self.column_policy = {
            str(table).lower(): {str(col).lower() for col in (spec.get("deny") or ())}
            for table, spec in (policy or {}).items()
        }
        # Live {table: [columns]} for SELECT * expansion. None means
        # unknown: stars on tables with any denied column fail closed.
        self.schema_columns = (
            {
                str(table).lower(): [str(col).lower() for col in cols]
                for table, cols in schema_columns.items()
            }
            if schema_columns
            else None
        )

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
            column_check = self._check_columns(expression, tables_lower, tables, "DELETE")
            if column_check:
                return column_check
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
            column_check = self._check_columns(expression, tables_lower, tables, "UPDATE")
            if column_check:
                return column_check
            return GuardrailResult(
                verdict=VERDICT_ALLOWED,
                statement_type="UPDATE",
                tables=tables,
            )

        if isinstance(expression, exp.Select):
            table_check = self._check_tables(tables_lower)
            if table_check:
                return table_check
            column_check = self._check_columns(expression, tables_lower, tables, "SELECT")
            if column_check:
                return column_check
            return GuardrailResult(
                verdict=VERDICT_ALLOWED,
                statement_type="SELECT",
                tables=tables,
            )

        if isinstance(expression, exp.Insert):
            table_check = self._check_tables(tables_lower)
            if table_check:
                return table_check
            column_check = self._check_columns(expression, tables_lower, tables, "INSERT")
            if column_check:
                return column_check
            # No explicit column list: a whole-row write touches every
            # column, so any denied column on the target fails closed.
            if not _insert_has_column_list(expression):
                for tbl in tables_lower:
                    denied = self.column_policy.get(tbl, set())
                    if denied:
                        return GuardrailResult(
                            verdict=VERDICT_BLOCKED,
                            reason=(
                                f"INSERT without a column list touches every column of "
                                f"'{tbl}', including denied column(s): "
                                f"{', '.join(sorted(denied))}."
                            ),
                            statement_type="INSERT",
                            tables=tables,
                        )
            # Volume gate mirrors the UPDATE/DELETE bulk-write model:
            # big batches — or batches whose size cannot be bounded
            # (INSERT..SELECT) — require approval instead of executing.
            row_count = _insert_row_count(expression)
            if row_count is None:
                return GuardrailResult(
                    verdict=VERDICT_REQUIRES_APPROVAL,
                    reason=(
                        "INSERT row count could not be estimated (non-VALUES "
                        f"source), so it cannot be shown to stay within the "
                        f"threshold ({config.RISKY_ROW_THRESHOLD} rows). "
                        f"This operation requires approval."
                    ),
                    statement_type="INSERT",
                    tables=tables,
                )
            if row_count > config.RISKY_ROW_THRESHOLD:
                return GuardrailResult(
                    verdict=VERDICT_REQUIRES_APPROVAL,
                    reason=(
                        f"INSERT of {row_count} rows "
                        f"(threshold: {config.RISKY_ROW_THRESHOLD}). "
                        f"This bulk operation requires approval."
                    ),
                    statement_type="INSERT",
                    tables=tables,
                )
            return GuardrailResult(
                verdict=VERDICT_ALLOWED,
                statement_type="INSERT",
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

    def _resolve_tables(self, qualifier, aliases, tables_lower):
        """Candidate tables for one column reference (fail-closed).

        A known qualifier (table or alias) pins to one table; anything
        unqualified-but-unambiguous (single-table query) pins to it; all
        other cases check the union so ambiguity can never hide a denial.
        """
        if qualifier:
            real = aliases.get(qualifier, qualifier)
            if real in tables_lower:
                return [real]
            return list(tables_lower)
        return list(tables_lower)

    def _check_columns(self, expression, tables_lower, tables, statement_type):
        """Enforce COLUMN_POLICY on every referenced column (BLOCK).

        Covers SELECT/WHERE/JOIN columns, INSERT targets, and UPDATE
        targets, including via SELECT * expansion.
        """
        aliases = _table_aliases(expression)
        cols, star_quals = _iter_columns(expression)

        for qualifier, name in cols:
            for tbl in self._resolve_tables(qualifier, aliases, tables_lower):
                if name in self.column_policy.get(tbl, set()):
                    return GuardrailResult(
                        verdict=VERDICT_BLOCKED,
                        reason=f"Column '{tbl}.{name}' is denied by policy.",
                        statement_type=statement_type,
                        tables=tables,
                    )

        for qualifier in star_quals:
            targets = (
                self._resolve_tables(qualifier, aliases, tables_lower)
                if qualifier
                else list(tables_lower)
            )
            for tbl in targets:
                denied = self.column_policy.get(tbl, set())
                if not denied:
                    continue
                known = (self.schema_columns or {}).get(tbl)
                if known is None:
                    return GuardrailResult(
                        verdict=VERDICT_BLOCKED,
                        reason=(
                            f"SELECT * on '{tbl}' cannot be proven safe: the "
                            f"table denies column(s) and its schema is unknown."
                        ),
                        statement_type=statement_type,
                        tables=tables,
                    )
                hit = sorted(set(known) & denied)
                if hit:
                    return GuardrailResult(
                        verdict=VERDICT_BLOCKED,
                        reason=(
                            f"SELECT * on '{tbl}' expands to denied column(s): "
                            f"{', '.join(hit)}."
                        ),
                        statement_type=statement_type,
                        tables=tables,
                    )
        return None
