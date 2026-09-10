"""
Run a .sql file against the OpsLens SQLite database and print each result set.

Usage:
    python run_sql.py sql/product_health.sql
"""

import sqlite3
import sys

import pandas as pd

DB_PATH = "data/opslens.db"


def split_statements(sql_text: str) -> list[str]:
    """Strip comment lines, then split the file into individual statements."""
    lines = [ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")]
    body = "\n".join(lines)
    return [s.strip() for s in body.split(";") if s.strip()]


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python run_sql.py <path-to-sql-file>")
        sys.exit(1)

    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as fh:
        statements = split_statements(fh.read())

    con = sqlite3.connect(DB_PATH)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 50)

    query_number = 0
    for statement in statements:
        # DDL (CREATE VIEW / DROP VIEW) returns no rows - just execute it.
        if statement.upper().startswith(("DROP", "CREATE", "PRAGMA")):
            con.execute(statement)
            continue

        query_number += 1
        result = pd.read_sql(statement, con)
        print(f"\n{'=' * 70}")
        print(f"QUERY {query_number}")
        print("=" * 70)
        print(result.to_string(index=False))

    con.close()
    print()


if __name__ == "__main__":
    main()
