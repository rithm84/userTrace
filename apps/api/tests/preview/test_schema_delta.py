"""A candidate's schema file is applied in place only when it adds and removes nothing.

The comparison is statement by statement, so these are plain unit tests: whether a
spare's database follows the change in place or restarts to install it afresh.
"""

from __future__ import annotations

from app.preview.schema_delta import SchemaMode, schema_delta, split_statements

BASE = """
-- The fixture's tables.
create table products (id uuid primary key, name text not null);
create table reviews (id uuid primary key, product_id uuid not null, topic text);
create index reviews_product on reviews (product_id);
"""

SAVED_VIEWS = """
create table saved_views (id uuid primary key, name text not null, filters jsonb not null);
"""


def test_an_unchanged_schema_file_changes_nothing() -> None:
    reformatted = BASE.replace("create table products", "CREATE   TABLE products")
    delta = schema_delta(base=BASE, candidate=reformatted)
    assert delta.mode is SchemaMode.UNCHANGED
    assert delta.statements == ()


def test_added_tables_indexes_and_columns_apply_in_place() -> None:
    candidate = (
        BASE
        + SAVED_VIEWS
        + "create unique index saved_views_name on saved_views (name);\n"
        + "alter table products add column archived boolean not null default false;\n"
        + "comment on table saved_views is 'A named filter stack';\n"
    )
    delta = schema_delta(base=BASE, candidate=candidate)
    assert delta.mode is SchemaMode.IN_PLACE
    assert [statement.split()[0] for statement in delta.statements] == [
        "create",
        "create",
        "alter",
        "comment",
    ]


def test_a_guarded_drop_of_a_table_the_change_creates_applies_in_place() -> None:
    candidate = BASE + "drop table if exists saved_views cascade;\n" + SAVED_VIEWS
    assert schema_delta(base=BASE, candidate=candidate).mode is SchemaMode.IN_PLACE
    # Dropping a table the base has is destructive, guarded or not.
    dropping = BASE + "drop table if exists reviews;\n"
    assert schema_delta(base=BASE, candidate=dropping).mode is SchemaMode.RESTART


def test_a_removed_or_altered_statement_restarts_the_spare() -> None:
    altered = BASE.replace("topic text", "topic text not null")
    assert schema_delta(base=BASE, candidate=altered).mode is SchemaMode.RESTART
    removed = BASE.replace("create index reviews_product on reviews (product_id);", "")
    assert schema_delta(base=BASE, candidate=removed).mode is SchemaMode.RESTART
    renamed = BASE + "alter table reviews rename column topic to subject;\n"
    assert schema_delta(base=BASE, candidate=renamed).mode is SchemaMode.RESTART


def test_a_base_without_the_file_makes_every_statement_new() -> None:
    delta = schema_delta(base=None, candidate=SAVED_VIEWS)
    assert delta.mode is SchemaMode.IN_PLACE
    assert len(delta.statements) == 1


def test_the_digest_names_the_change_not_its_spacing() -> None:
    spaced = BASE + SAVED_VIEWS.replace(" (", "  (").upper()
    assert schema_delta(base=BASE, candidate=spaced).digest == (
        schema_delta(base=BASE, candidate=BASE + SAVED_VIEWS).digest
    )
    assert split_statements("-- only a comment;\n") == ()
