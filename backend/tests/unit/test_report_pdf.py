"""Server-side PDF rendering: valid documents for empty, wide, long and hostile content."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("reportlab")

from app.services import report_pdf  # noqa: E402
from app.services.reports import CsvRenderer, RenderedReport, render  # noqa: E402


def _report(columns: list[str], rows: list[list[object]]) -> RenderedReport:
    return RenderedReport(
        key="test-report",
        title="Test <report> & co",
        description="Unit test",
        columns=columns,
        rows=rows,
        generated_at=datetime(2026, 10, 19, 8, 0, tzinfo=UTC),
        generated_for="alice",
        scope=["server PG-1"],
        version="0.14.0",
    )


def test_empty_report_is_a_valid_pdf() -> None:
    data = report_pdf.PdfRenderer().render(_report(["a", "b"], []))
    assert data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")


def test_long_wide_report_paginates_and_escapes_markup() -> None:
    cols = [f"column_{i}" for i in range(12)]
    rows = [[f"<b>row {r}</b> & {'x' * 900}" if c == 0 else r * c for c in range(12)] for r in range(400)]
    data = report_pdf.PdfRenderer().render(_report(cols, rows))
    assert data.startswith(b"%PDF-")
    assert data.count(b"/Type /Page") - data.count(b"/Type /Pages") > 5


def test_row_cap(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(report_pdf, "MAX_ROWS", 10)
    small = report_pdf.PdfRenderer().render(_report(["n"], [[i] for i in range(10)]))
    capped = report_pdf.PdfRenderer().render(_report(["n"], [[i] for i in range(5000)]))
    assert len(capped) < len(small) * 3


def test_render_dispatch_and_filenames() -> None:
    rep = _report(["a"], [[1]])
    data, media, name = render(rep, "pdf")
    assert media == "application/pdf" and name.endswith(".pdf") and data.startswith(b"%PDF")
    data, media, name = render(rep, "csv")
    assert media.startswith("text/csv") and name.endswith(".csv")
    assert CsvRenderer().render(rep).decode().splitlines()[0] == "a"
    with pytest.raises((KeyError, ValueError)):
        render(rep, "xlsx")


def test_csv_neutralises_formulas_but_not_numbers() -> None:
    rep = _report(["a", "b", "c"], [['=HYPERLINK("http://x")', -5, "@SUM(A1)"], ["+cmd|' /C calc'!A0", 1.5, "plain"]])
    lines = CsvRenderer().render(rep).decode().splitlines()
    assert lines[1].startswith("\"'=HYPERLINK") and ",-5," in lines[1] and "'@SUM(A1)" in lines[1]
    assert lines[2].startswith("'+cmd") and lines[2].endswith(",1.5,plain")
