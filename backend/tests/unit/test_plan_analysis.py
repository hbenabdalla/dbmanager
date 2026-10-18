from app.adapters.postgres.adapter import PostgreSQLAdapter
from app.domain.plan_analysis import analyze_plan

PLAN = {
    "Node Type": "Sort",
    "Total Cost": 50000.0,
    "Plan Rows": 2000000,
    "Actual Rows": 2000000,
    "Sort Space Type": "Disk",
    "Plans": [
        {
            "Node Type": "Nested Loop",
            "Join Type": "Inner",
            "Total Cost": 48000.0,
            "Plan Rows": 100,
            "Actual Rows": 2000000,
            "Plans": [
                {
                    "Node Type": "Seq Scan",
                    "Relation Name": "orders",
                    "Schema": "public",
                    "Total Cost": 20000.0,
                    "Plan Rows": 900000,
                    "Actual Rows": 900000,
                    "Actual Loops": 1,
                    "Filter": "(status = 'x')",
                    "Rows Removed by Filter": 50000000,
                    "Shared Read Blocks": 200000,
                },
                {
                    "Node Type": "Index Scan",
                    "Relation Name": "customers",
                    "Index Name": "customers_pkey",
                    "Total Cost": 8.0,
                    "Plan Rows": 1,
                    "Actual Rows": 1,
                    "Actual Loops": 900000,
                },
            ],
        }
    ],
}


def test_convert_and_detect() -> None:
    root = PostgreSQLAdapter._convert_plan(PLAN, [0])
    assert root.operator == "Sort" and root.children[0].operator == "Nested Loop (Inner)"
    assert root.children[0].children[0].relation == "public.orders"
    codes = {i.code for i in analyze_plan(root, analyzed=True)}
    assert {
        "FULL_SCAN",
        "SORT_SPILL",
        "EXPENSIVE_NESTED_LOOP",
        "CARDINALITY_MISESTIMATE",
        "INEFFICIENT_FILTER",
        "EXCESSIVE_READS",
    } <= codes


def test_estimate_mode_ignores_actuals() -> None:
    root = PostgreSQLAdapter._convert_plan(PLAN, [0])
    codes = {i.code for i in analyze_plan(root, analyzed=False)}
    assert "CARDINALITY_MISESTIMATE" not in codes
