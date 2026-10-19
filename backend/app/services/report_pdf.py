"""Server-side PDF rendering of reports (ReportLab, BSD licence).

The document carries the DBManager logo, the report title and description, who it was
generated for, when, the scope and the row count, then the table (header repeated on every
page, landscape when the table is wide). Every page footer says the file contains data visible
to that principal — reports are snapshots of what the caller may see, not of the whole fleet.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.reports import RenderedReport

LOGO = Path(__file__).resolve().parents[1] / "data" / "brand" / "dbmanager-logo.png"
#: a cell is cut after this many characters (the CSV keeps the full value)
MAX_CELL = 400
#: the PDF lists at most this many rows; the CSV has all of them
MAX_ROWS = 5000

NAVY = "#13263f"
MUTED = "#5b6b80"
GRID = "#d5dde8"
ZEBRA = "#f4f7fb"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pdf_cell(v: Any) -> str:
    """Like the CSV value, with dates to the minute (the CSV keeps full ISO timestamps)."""
    from app.services.reports import cell

    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    return cell(v)


def _column_widths(columns: list[str], sample: list[list[Any]], width: float, font_size: float) -> list[float]:
    """Every column at least as wide as its longest word (headers never break mid-word), the
    remaining width shared by how much text each column holds."""
    cw = font_size * 0.55  # average Helvetica glyph width
    pad = 7.0
    minw, want = [], []
    for i, c in enumerate(columns):
        values = [_pdf_cell(r[i])[:MAX_CELL] for r in sample]
        words = [len(w) for v in values for w in v.split()] + [len(w) for w in c.replace("_", " ").split()]
        longest_word = min(max(words, default=4), 16)
        avg = sum(len(v) for v in values) / len(values) if values else len(c)
        lo = longest_word * cw + pad
        minw.append(lo)
        want.append(max(lo, min(max(avg, len(c)) * 1.15, 60) * cw + pad))
    total_min, total_want = sum(minw), sum(want)
    if total_want <= width:
        return [w * width / total_want for w in want]
    if total_min >= width:
        return [w * width / total_min for w in minw]
    spare = width - total_min
    extra = [w - m for w, m in zip(want, minw, strict=True)]
    return [m + spare * e / sum(extra) for m, e in zip(minw, extra, strict=True)]


class PdfRenderer:
    media_type = "application/pdf"
    extension = "pdf"

    def render(self, report: RenderedReport) -> bytes:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Image, LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

        wide = len(report.columns) > 7
        pagesize = landscape(A4) if wide else A4
        margin = 12 * mm
        width = pagesize[0] - 2 * margin
        body = ParagraphStyle(
            "body", fontName="Helvetica", fontSize=6.6 if wide else 7.4, leading=8.4, alignment=TA_LEFT
        )
        head = ParagraphStyle("head", parent=body, fontName="Helvetica-Bold", textColor=colors.white)
        title = ParagraphStyle(
            "title", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=colors.HexColor(NAVY)
        )
        meta = ParagraphStyle("meta", fontName="Helvetica", fontSize=8, leading=10.5, textColor=colors.HexColor(MUTED))

        story: list[Any] = []
        if LOGO.exists():
            img = Image(str(LOGO))
            ratio = img.imageWidth / img.imageHeight
            img.drawHeight = 16 * mm
            img.drawWidth = 16 * mm * ratio
            img.hAlign = "LEFT"
            story += [img, Spacer(1, 3 * mm)]
        story.append(Paragraph(_escape(report.title), title))
        story.append(Paragraph(_escape(report.description), meta))
        when = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
        line = f"Generated {when} for {_escape(report.generated_for)} · DBManager {_escape(report.version)}"
        story.append(Paragraph(line, meta))
        rows = report.rows[:MAX_ROWS]
        scope = "; ".join(report.scope) if report.scope else "all servers visible to this account"
        count = f"{len(report.rows):,} rows" + (
            f" (first {MAX_ROWS:,} shown; the CSV has all)" if len(report.rows) > MAX_ROWS else ""
        )
        story.append(Paragraph(f"Scope: {_escape(scope)} · {count}", meta))
        story.append(Spacer(1, 4 * mm))

        if not report.rows:
            story.append(Paragraph("No rows: nothing matched for the servers this account can see.", meta))
        else:
            col_widths = _column_widths(report.columns, rows[:200], width, body.fontSize)
            data = [[Paragraph(_escape(c.replace("_", " ")), head) for c in report.columns]]
            for r in rows:
                data.append([Paragraph(_escape(_pdf_cell(v)[:MAX_CELL]), body) for v in r])
            table = LongTable(data, colWidths=col_widths, repeatRows=1)
            style = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor(GRID)),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
            style += [("BACKGROUND", (0, i), (-1, i), colors.HexColor(ZEBRA)) for i in range(2, len(data), 2)]
            table.setStyle(TableStyle(style))
            story.append(table)

        footer_text = f"{report.title} · contains data visible to {report.generated_for}; handle it according to your data classification"

        def footer(canvas: Any, doc: Any) -> None:
            canvas.saveState()
            canvas.setFont("Helvetica", 6.5)
            canvas.setFillColor(colors.HexColor(MUTED))
            canvas.drawString(margin, 7 * mm, footer_text[:180])
            canvas.drawRightString(pagesize[0] - margin, 7 * mm, f"Page {doc.page}")
            canvas.restoreState()

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=pagesize,
            leftMargin=margin,
            rightMargin=margin,
            topMargin=margin,
            bottomMargin=14 * mm,
            title=report.title,
            author="DBManager",
            subject=report.description,
            creator=f"DBManager {report.version}",
        )
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        return buf.getvalue()
