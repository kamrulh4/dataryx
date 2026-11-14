import re
import threading
from collections.abc import Generator
from typing import Any, Literal

import polars as pl
from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.engine import make_url

from core.configs import logger
from core.dataryx.database_connection_manager.db_connections import get_local_database_connection
from core.dataryx.flow_data_engine.flow_file_column.main import DataryxColumn
from core.dataryx.sources.external_sources.base_class import ExternalDataSource
from core.dataryx.sources.external_sources.sql_source.utils import construct_sql_uri, get_polars_type
from core.schemas.input_schema import DatabaseSettings, MinimalFieldInfo
from core.secret_manager.secret_manager import decrypt_secret, get_encrypted_secret

QueryMode = Literal["table", "query"]

# oracledb's (and SQLAlchemy's oracle dialect's) default cursor arraysize is
# only 100 rows -- every fetch, regardless of our own chunk_size setting,
# does one network round trip per 100 rows under the hood. Confirmed via a
# live repro against a real remote Oracle instance: fetching 100,000 rows
# with the default arraysize took ~105s; the SAME query with arraysize=10000
# took ~24s (and a narrower single-column 50,000-row fetch dropped from
# 38.85s to 0.61s). This was the actual root cause behind "chunking/reading
# is slow" reports -- our own chunk_size feature operates one layer above
# this and never fixed it. 10,000 is a safe default: ~14 narrow/medium
# columns x 10,000 rows is a couple MB of buffer per round trip, trivial.
_ORACLE_ARRAYSIZE = 10_000

# oracledb's call_timeout is a PER-ROUND-TRIP timeout (ms), not a total-
# operation timeout -- confirmed via python-oracledb's docs and by checking
# the connection attribute directly. A legitimate large fetch that takes
# many fast round trips is unaffected; only a single round trip that never
# gets a response (e.g. the bank VPN drops mid-fetch) is caught and the
# connection is force-closed instead of hanging indefinitely (client-
# reported: "connection hangs at times, you have to kill the application
# task"). There is no built-in call-level timeout at all without this --
# verified by connecting to a non-routable IP: the connect-phase itself
# gave up after oracledb's own internal ~20s default, but a stall AFTER a
# connection is already established has no such protection.
_ORACLE_CALL_TIMEOUT_MS = 60_000


def _create_sql_engine(connection_string: str) -> Engine:
    """create_engine(), with dialect-specific tuning applied where it's been
    tested and confirmed safe.

    Only Oracle gets special handling right now (see _ORACLE_ARRAYSIZE /
    _ORACLE_CALL_TIMEOUT_MS above) -- arraysize is an Oracle-dialect-only
    create_engine() kwarg; passing it for any other dialect (sqlite,
    postgresql, mysql, db2) raises TypeError immediately (confirmed), so
    every other database type must keep going through a plain
    create_engine(connection_string) call, completely unchanged.
    """
    try:
        backend = make_url(connection_string).get_backend_name()
    except Exception:
        return create_engine(connection_string)

    if backend != "oracle":
        return create_engine(connection_string)

    engine = create_engine(connection_string, arraysize=_ORACLE_ARRAYSIZE)

    @event.listens_for(engine, "connect")
    def _set_oracle_call_timeout(dbapi_connection, connection_record):
        dbapi_connection.call_timeout = _ORACLE_CALL_TIMEOUT_MS

    return engine


class UnsafeSQLError(ValueError):
    """Raised when a SQL query contains unsafe operations."""

    pass


def validate_sql_query(query: str) -> None:
    """
    Validate that a SQL query is safe for execution (read-only SELECT statements only).

    This function checks that the query:
    1. Is a SELECT statement (not INSERT, UPDATE, DELETE, etc.)
    2. Does not contain DDL statements (DROP, CREATE, ALTER, TRUNCATE)
    3. Does not contain other dangerous operations

    Args:
        query: The SQL query string to validate

    Raises:
        UnsafeSQLError: If the query contains unsafe operations
    """
    if not query or not query.strip():
        raise UnsafeSQLError("SQL query cannot be empty")

    # Normalize the query: remove comments and extra whitespace
    normalized = _remove_sql_comments(query)
    normalized = " ".join(normalized.split()).upper()

    # Check if query starts with SELECT (allowing for WITH clauses / CTEs)
    if not _is_select_query(normalized):
        raise UnsafeSQLError(
            "Only SELECT queries are allowed. "
            "The query must start with SELECT or WITH (for common table expressions)."
        )

    # Check for dangerous DDL statements
    ddl_patterns = [
        (r"\bDROP\s+", "DROP statements are not allowed"),
        (r"\bCREATE\s+", "CREATE statements are not allowed"),
        (r"\bALTER\s+", "ALTER statements are not allowed"),
        (r"\bTRUNCATE\s+", "TRUNCATE statements are not allowed"),
        (r"\bRENAME\s+", "RENAME statements are not allowed"),
    ]

    for pattern, error_msg in ddl_patterns:
        if re.search(pattern, normalized):
            raise UnsafeSQLError(error_msg)

    # Check for dangerous DML statements (these shouldn't appear in a SELECT)
    dml_patterns = [
        (r"\bINSERT\s+INTO\b", "INSERT statements are not allowed"),
        (r"\bUPDATE\s+\w+\s+SET\b", "UPDATE statements are not allowed"),
        (r"\bDELETE\s+FROM\b", "DELETE statements are not allowed"),
    ]

    for pattern, error_msg in dml_patterns:
        if re.search(pattern, normalized):
            raise UnsafeSQLError(error_msg)

    # Check for dangerous operations that could be used maliciously
    dangerous_patterns = [
        (r"\bEXEC(UTE)?\s*\(", "EXECUTE statements are not allowed"),
        (r"\bCALL\s+", "CALL statements (stored procedures) are not allowed"),
        (r"\bGRANT\s+", "GRANT statements are not allowed"),
        (r"\bREVOKE\s+", "REVOKE statements are not allowed"),
    ]

    for pattern, error_msg in dangerous_patterns:
        if re.search(pattern, normalized):
            raise UnsafeSQLError(error_msg)


def _remove_sql_comments(query: str) -> str:
    """
    Remove SQL comments from a query string.

    Handles:
    - Single line comments (-- comment)
    - Multi-line comments (/* comment */)
    """
    # Remove multi-line comments using a non-backtracking pattern
    # Matches /* followed by (non-* chars OR * not followed by /) then */
    result = re.sub(r"/\*(?:[^*]|\*(?!/))*\*/", " ", query)
    # Remove single-line comments - explicitly match non-newline chars to avoid backtracking
    result = re.sub(r"--[^\r\n]*", " ", result)
    return result


def _is_select_query(normalized_query: str) -> bool:
    """
    Check if a normalized (uppercase, whitespace-cleaned) query is a SELECT statement.

    Allows:
    - SELECT ...
    - WITH ... SELECT ... (CTEs)
    """
    # Check for direct SELECT
    if normalized_query.startswith("SELECT ") or normalized_query.startswith("SELECT\t"):
        return True

    # Check for WITH clause (CTE) that leads to SELECT
    if normalized_query.startswith("WITH ") or normalized_query.startswith("WITH\t"):
        # CTEs should eventually have a SELECT
        # Make sure there's a SELECT after the WITH clause and no dangerous statements
        if " SELECT " in normalized_query or "\tSELECT " in normalized_query:
            return True

    return False


def get_query_columns(engine: Engine, query_text: str):
    """
    Get column names from a query and assume string type for all columns

    Args:
        engine: SQLAlchemy engine object
        query_text: SQL query as a string

    Returns:
        Dictionary mapping column names to string type
    """
    with engine.connect() as connection:
        # Create a text object from the query
        query = text(query_text)

        # Execute the query to get column names
        result = connection.execute(query)
        column_names = result.keys()
        result.close()  # Close the result to avoid consuming the cursor

        return list(column_names)


def get_table_column_types(engine: Engine, table_name: str, schema: str = None) -> list[tuple[str, Any]]:
    """
    Get column types from a database table using a SQLAlchemy engine

    Args:
        engine: SQLAlchemy engine object
        table_name: Name of the table to inspect
        schema: Optional schema name (e.g., 'public' for PostgreSQL)

    Returns:
        Dictionary mapping column names to their SQLAlchemy types
    """
    inspector = inspect(engine)
    columns = inspector.get_columns(table_name, schema=schema)

    return [(column["name"], column["type"]) for column in columns]


class BaseSqlSource:
    """
    A simplified base class for SQL sources that handles query generation
    without requiring database connection details.
    """

    table_name: str | None = None
    query: str | None = None
    schema_name: str | None = None
    query_mode: QueryMode = "table"
    schema: list[DataryxColumn] | None = None

    def __init__(
        self,
        query: str = None,
        table_name: str = None,
        schema_name: str = None,
        fields: list[MinimalFieldInfo] | None = None,
    ):
        """
        Initialize a BaseSqlSource object.

        Args:
            query: SQL query string (if query_mode is 'query')
            table_name: Name of the table to query (if query_mode is 'table')
            schema_name: Optional database schema name
            fields: Optional list of field information
        """
        if schema_name == "":
            schema_name = None

        # Validate inputs
        if query is not None and table_name is not None:
            raise ValueError("Only one of table_name or query can be provided")
        if query is None and table_name is None:
            raise ValueError("Either table_name or query must be provided")

        # Set query mode and build query if needed
        if query is not None:
            # Validate user-provided queries for safety (read-only SELECT only)
            validate_sql_query(query)
            self.query_mode = "query"
            # A trailing ";" is normal in SQL editors but breaks both the
            # wrapped schema-sample query and some drivers (DB2), so drop it.
            self.query = query.strip().rstrip(";").rstrip()
        elif table_name is not None:
            self.query_mode = "table"
            self.table_name = table_name
            self.schema_name = schema_name

            # Generate the basic query
            if schema_name is not None and schema_name != "":
                self.query = f"SELECT * FROM {schema_name}.{table_name}"
            else:
                self.query = f"SELECT * FROM {table_name}"

        # Set schema if provided
        if fields:
            self.schema = [DataryxColumn.from_input(column_name=col.name, data_type=col.data_type) for col in fields]

    def get_sample_query(self, dialect: str | None = None) -> str:
        """
        Get a sample query that returns a limited number of rows.

        Oracle and DB2 don't support the `LIMIT` clause (confirmed live
        against a real Oracle instance: raises ORA-00933 "SQL command not
        properly ended"). They use the ANSI SQL:2008 `FETCH FIRST n ROWS
        ONLY` clause instead. Oracle *also* rejects the `AS` keyword before
        a subquery alias ("select * from (...) as x" -> ORA-00933 too) --
        omitting `AS` is valid there and everywhere else this runs
        (Postgres/MySQL/SQLite all accept a bare alias), so it's dropped
        unconditionally rather than only for Oracle/DB2.
        """
        if dialect in ("oracle", "ibm_db_sa"):
            if self.query_mode == "query":
                # DB2's isolation clause (WITH UR/CS/RS/RR) is only valid at the
                # very end of the statement, not inside a subquery. Left inside,
                # the schema lookup failed (seen on a client's DB2 query
                # "... WITH UR;"), so move it after FETCH FIRST.
                inner, isolation = self.query, ""
                if dialect == "ibm_db_sa":
                    # "FOR READ ONLY" / "FOR FETCH ONLY" / "OPTIMIZE FOR n ROWS"
                    # are also end-of-statement-only on DB2 (a bank query ended
                    # "... ) x FOR READ ONLY WITH UR;" -> SQL0104N). They don't
                    # matter for this one-row lookup, so drop them. The clauses
                    # can come in any order, hence the loop.
                    while True:
                        match = re.search(r"\s+(WITH\s+(UR|CS|RS|RR))\s*$", inner, re.IGNORECASE)
                        if match:
                            inner, isolation = inner[: match.start()], " " + match.group(1)
                            continue
                        match = re.search(
                            r"\s+(FOR\s+(READ|FETCH)\s+ONLY|OPTIMIZE\s+FOR\s+\d+\s+ROWS?)\s*$",
                            inner,
                            re.IGNORECASE,
                        )
                        if match:
                            inner = inner[: match.start()]
                            continue
                        break
                return f"select * from ({inner}) main_query FETCH FIRST 1 ROWS ONLY{isolation}"
            else:
                return f"{self.query} FETCH FIRST 1 ROWS ONLY"
        if self.query_mode == "query":
            return f"select * from ({self.query}) main_query LIMIT 1"
        else:
            return f"{self.query} LIMIT 1"

    @staticmethod
    def _parse_table_name(table_name: str) -> tuple[str | None, str]:
        """
        Parse a table name that may include a schema.

        Args:
            table_name: Table name possibly in the format 'schema.table'

        Returns:
            Tuple of (schema, table_name)
        """
        table_parts = table_name.split(".")
        if len(table_parts) > 1:
            # Handle schema.table_name format
            schema = ".".join(table_parts[:-1])
            table = table_parts[-1]
            return schema, table
        else:
            return None, table_name


class SqlSource(BaseSqlSource, ExternalDataSource):
    connection_string: str | None
    read_result: pl.DataFrame | None = None
    driver: str = "sqlalchemy"   # "sqlalchemy" | "connectorx"

    def __init__(
        self,
        connection_string: str,
        query: str = None,
        table_name: str = None,
        schema_name: str = None,
        fields: list[MinimalFieldInfo] | None = None,
        driver: str = "sqlalchemy",
        chunk_size: int | None = None,
    ):
        # Initialize the base class first
        BaseSqlSource.__init__(self, query=query, table_name=table_name, schema_name=schema_name, fields=fields)

        # Set connection-specific attributes
        self.connection_string = connection_string
        self.read_result = None
        self.driver = driver or "sqlalchemy"
        # Only meaningful for driver="sqlalchemy" -- connectorx's read path
        # (pl.read_database_uri) has no batching parameter, it does its own
        # internal fetching in Rust, so chunk_size is ignored there.
        self.chunk_size = chunk_size if chunk_size and chunk_size > 0 else None
        # Set by cancel(); _read_database() stops waiting for the query.
        self._cancel_event = threading.Event()
        # DBAPI connection of the running read, used by cancel() (Oracle).
        self._active_dbapi_connection = None

    def cancel(self) -> None:
        """Stop waiting for a running query so the app is released at once.

        Oracle: the query is also killed on the database (connection.cancel()).
        Other drivers (e.g. DB2's ibm_db) have no cancel call, so the query
        keeps running on the database and its connection is closed when it
        finishes.
        """
        self._cancel_event.set()
        dbapi_connection = self._active_dbapi_connection
        if dbapi_connection is not None and hasattr(dbapi_connection, "cancel"):
            try:
                dbapi_connection.cancel()
                logger.info("Cancel sent to the database")
            except Exception as ex:
                logger.warning(f"Could not cancel the query on the database: {ex}")
        elif dbapi_connection is not None:
            logger.info("This database driver cannot cancel a running query; "
                        "it keeps running on the database until it finishes")

    def get_initial_data(self) -> list[dict[str, Any]]:
        return []

    def validate(self) -> None:
        try:
            engine = _create_sql_engine(self.connection_string)
            if self.query_mode == "table":
                try:
                    if self.schema_name is not None:
                        self._get_columns_from_table_and_schema(engine, self.table_name, self.schema_name)
                    if self.table_name is not None:
                        self._get_columns_from_table(engine, self.table_name)
                except Exception as e:
                    logger.warning(f"Error getting column info for table {self.table_name}: {e}")
                    c = self._get_columns_from_query(engine, self.get_sample_query(dialect=engine.dialect.name))
                    if len(c) == 0:
                        raise ValueError("No columns found in the query")
            else:
                c = self._get_columns_from_query(engine, self.get_sample_query(dialect=engine.dialect.name))
                if len(c) == 0:
                    raise ValueError("No columns found in the query")
        except Exception as e:
            logger.error(f"Error validating SQL source: {e}")
            raise e

    def get_iter(self) -> Generator[dict[str, Any], None, None]:
        logger.warning("Getting data in iteration, this is suboptimal")
        data = self.data_getter()
        for row in data:
            yield row

    def get_df(self):
        df = self.get_pl_df()
        return df.to_pandas()

    def get_sample(self, n: int = 10000) -> Generator[dict[str, Any], None, None]:
        if self.query_mode == "table":
            query = f"{self.query} LIMIT {n}"
            try:
                df = self._read_database(query)
                return (r for r in df.to_dicts())
            except Exception as e:
                logger.error(f"Error with query: {query}")
                raise e
        else:
            df = self.get_pl_df()
            rows = df.head(n).to_dicts()
            return (r for r in rows)

    def data_getter(self) -> Generator[dict[str, Any], None, None]:
        df = self.get_pl_df()
        rows = df.to_dicts()
        return (r for r in rows)

    def _read_database(self, query: str) -> pl.DataFrame:
        """Run _read_database_blocking() in a background thread and wait for it,
        so a Cancel can release the caller while the query is still running."""
        outcome = {}

        def _worker():
            try:
                outcome["result"] = self._read_database_blocking(query)
            except BaseException as ex:
                outcome["error"] = ex
            if self._cancel_event.is_set():
                # Nobody waits for this result any more (the run was canceled).
                if "error" in outcome:
                    logger.info(f"Canceled query stopped on the database: {outcome['error']}")
                else:
                    logger.info("Canceled query finished on the database; result discarded")

        worker = threading.Thread(target=_worker, name="dataryx-sql-read", daemon=True)
        worker.start()
        while worker.is_alive():
            if self._cancel_event.is_set():
                raise RuntimeError("Query canceled by user")
            worker.join(0.2)
        if "error" in outcome:
            raise outcome["error"]
        return outcome["result"]

    def _read_database_blocking(self, query: str) -> pl.DataFrame:
        """Execute a query using the configured driver (sqlalchemy or connectorx)."""
        if self.driver == "connectorx":
            # Connector/X needs clean URI without dialect suffixes like +psycopg2
            cx_uri = self._connectorx_uri(self.connection_string)
            return pl.read_database_uri(query, cx_uri, engine="connectorx")

        # "sqlalchemy" driver -- must go through pl.read_database(query, connection)
        # with a real SQLAlchemy engine/connection object. pl.read_database_uri()
        # (used above for connectorx) only accepts engine="connectorx"/"adbc" and
        # silently defaults to connectorx if no engine is given -- so calling it
        # here would ALWAYS use connectorx under the hood regardless of the
        # "sqlalchemy" driver selection, ignoring any SQLAlchemy-specific dialect
        # (e.g. connection strings/features connectorx doesn't support).
        engine = _create_sql_engine(self.connection_string)
        try:
            # stream_results tells dialects that support server-side/named
            # cursors (e.g. psycopg2) not to buffer the full result client-side
            # before returning. Oracle's actual arraysize-based batching is
            # tuned in _create_sql_engine() above instead -- the default
            # arraysize (100) was confirmed to be the real bottleneck behind
            # "reads are slow" reports, not this option.
            with engine.connect().execution_options(stream_results=True) as connection:
                try:
                    self._active_dbapi_connection = connection.connection.dbapi_connection
                except Exception:
                    self._active_dbapi_connection = None
                if self.chunk_size:
                    # Client-requested (large DB2 pulls freezing/timing out):
                    # fetch in batches via the cursor instead of one single
                    # fetch-everything call. This is the "simple" option we
                    # agreed on -- it does not add resumability, a dropped
                    # connection mid-read still fails the whole read and it
                    # must be re-run from the start. What it does do: lower
                    # peak memory during the fetch, and keep the connection
                    # actively transferring data across several smaller
                    # fetches instead of one long call with no visible
                    # progress, which real firewalls are more likely to kill
                    # as "idle" than a connection with steady traffic.
                    batches = list(
                        pl.read_database(
                            query,
                            connection,
                            iter_batches=True,
                            batch_size=self.chunk_size,
                            infer_schema_length=None,
                        )
                    )
                    if batches:
                        # vertical_relaxed: each batch infers its own dtypes, so
                        # a column that is all-NULL in one batch comes back as
                        # Null there and as String/Float64 in another. A plain
                        # concat raised SchemaError on that; relaxed casts to
                        # the common type instead.
                        return pl.concat(batches, how="vertical_relaxed")
                    # A 0-row result yields zero batches (nothing to concat),
                    # not one empty-but-correctly-typed batch -- fall through
                    # to a plain re-read on the same connection so the caller
                    # still gets a DataFrame with the right columns/dtypes
                    # instead of pl.concat([]) raising.
                # infer_schema_length=None: Polars guesses each column's type
                # from the first 100 rows by default. A column that is NULL in
                # all of those (very common for optional fields) was typed as
                # Null and the read crashed at the first real value ("could not
                # append value ... to the builder"), confirmed on Oracle.
                return pl.read_database(query, connection, infer_schema_length=None)
        finally:
            self._active_dbapi_connection = None
            engine.dispose()

    @staticmethod
    def _connectorx_uri(uri: str) -> str:
        """Strip SQLAlchemy dialect suffixes for Connector/X compatibility.
        e.g. postgresql+psycopg2://... -> postgresql://...
        """
        import re
        return re.sub(r'(\w+)\+\w+(://)', r'\1\2', uri)

    def get_pl_df(self) -> pl.DataFrame:
        if self.read_result is None:
            self.read_result = self._read_database(self.query)
        return self.read_result

    def get_flow_file_columns(self) -> list[DataryxColumn]:
        """
        Get column information from the SQL source and convert to DataryxColumn objects

        Returns:
            List of DataryxColumn objects representing the columns in the SQL source
        """
        engine = _create_sql_engine(self.connection_string)

        if self.query_mode == "table":
            try:
                if self.schema_name is not None:
                    return self._get_columns_from_table_and_schema(engine, self.table_name, self.schema_name)
                if self.table_name is not None:
                    return self._get_columns_from_table(engine, self.table_name)
            except Exception as e:
                logger.error(f"Error getting column info for table {self.table_name}: {e}")

        return self._get_columns_from_query(engine, self.get_sample_query(dialect=engine.dialect.name))

    @staticmethod
    def _get_columns_from_table(engine: Engine, table_name: str) -> list[DataryxColumn]:
        """
        Get DataryxColumn objects from a database table

        Args:
            engine: SQLAlchemy engine
            table_name: Name of the table (possibly including schema)

        Returns:
            List of DataryxColumn objects
        """
        schema_name, table = BaseSqlSource._parse_table_name(table_name)
        column_types = get_table_column_types(engine, table, schema=schema_name)
        columns = [
            DataryxColumn.create_from_polars_dtype(column_name, get_polars_type(column_type))
            for column_name, column_type in column_types
        ]

        return columns

    @staticmethod
    def _get_columns_from_table_and_schema(engine: Engine, table_name: str, schema_name: str):
        """
        Get DataryxColumn objects from a database table

        Args:
            engine: SQLAlchemy engine
            table_name: Name of the table (possibly including schema)
            schema_name: Name of the schema
        Returns:
            List of DataryxColumn objects
        """
        column_types = get_table_column_types(engine, table_name, schema=schema_name)
        columns = [
            DataryxColumn.create_from_polars_dtype(column_name, get_polars_type(column_type))
            for column_name, column_type in column_types
        ]
        return columns

    @staticmethod
    def _get_columns_from_query(engine: Engine, query: str) -> list[DataryxColumn]:
        """
        Get DataryxColumn objects from a SQL query

        Args:
            engine: SQLAlchemy engine
            query: SQL query string

        Returns:
            List of DataryxColumn objects
        """
        # Read the (one-row) sample the same way the real read does, so names
        # and types match what Run produces. result.keys() lower-cased Oracle
        # names ("amount" vs "AMOUNT") and everything was typed String, so
        # column pickers before a Run didn't match the actual data.
        try:
            with engine.connect() as connection:
                sample = pl.read_database(query, connection, infer_schema_length=None)
            return [
                DataryxColumn.create_from_polars_dtype(name, pl.String() if dtype == pl.Null else dtype)
                for name, dtype in sample.schema.items()
            ]
        except Exception as e:
            logger.warning(f"Typed column lookup failed, falling back to names only: {e}")
        try:
            column_names = get_query_columns(engine, query)

            columns = [
                DataryxColumn.create_from_polars_dtype(column_name, pl.String()) for column_name in column_names
            ]
            return columns
        except Exception as e:
            logger.error(f"Error getting column info for query: {e}")
            raise e

    def parse_schema(self) -> list[DataryxColumn]:
        return self.get_schema()

    def get_schema(self) -> list[DataryxColumn]:
        if self.schema is None:
            self.schema = self.get_flow_file_columns()
        return self.schema


def create_sql_source_from_db_settings(database_settings: DatabaseSettings, user_id: int) -> SqlSource:
    database_connection = database_settings.database_connection
    if database_settings.connection_mode == "inline":
        if database_connection is None:
            raise ValueError("Database connection is required in inline mode")
        encrypted_secret = get_encrypted_secret(current_user_id=user_id, secret_name=database_connection.password_ref)
    else:
        database_connection = get_local_database_connection(database_settings.database_connection_name, user_id)
        encrypted_secret = database_connection.password.get_secret_value()
    if encrypted_secret is None:
        raise ValueError(f"Secret with name {database_connection.password_ref} not found for user {user_id}")

    sql_source = SqlSource(
        connection_string=construct_sql_uri(
            database_type=database_connection.database_type,
            host=database_connection.host,
            port=database_connection.port,
            database=database_connection.database,
            username=database_connection.username,
            password=decrypt_secret(encrypted_secret),
            oracle_connect_type=getattr(
                database_connection, "oracle_connect_type", "service_name"
            )
            or "service_name",
        ),
        query=None if database_settings.query_mode == "table" else database_settings.query,
        table_name=database_settings.table_name,
        schema_name=database_settings.schema_name,
    )
    return sql_source
