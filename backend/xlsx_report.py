"""Build the people-counter report as a real .xlsx workbook.

The workbook carries the numbers and two native Excel bar charts that point at
the cells holding them, so what opens in Excel is the exported data rather than
a picture of it. Written against ECMA-376 with nothing but the standard library:
an xlsx is a zip of xml parts, and this needs no general-purpose spreadsheet
library to produce four sheets and two charts.

Kept in step with the platform's TypeScript version (backend/src/utils/
people-report.xlsx.ts on the FactoryBox server) so both exports look alike.
"""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

IN_COLOR = "3987E5"
OUT_COLOR = "D95926"
CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DRAW_NS = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
MAIN_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _col_name(index: int) -> str:
    n, out = index + 1, ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _cell(ref: str, value, style: int) -> str:
    if value is None or value == "":
        return ""
    s = f' s="{style}"' if style else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{s}><v>{value}</v></c>'
    return f'<c r="{ref}"{s} t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'


class _Chart:
    """Where on a sheet the plotted numbers live."""

    def __init__(self, title, first_row, last_row, label_col, in_col, out_col, anchor_col):
        self.title = title
        self.first_row = first_row
        self.last_row = last_row
        self.label_col = label_col
        self.in_col = in_col
        self.out_col = out_col
        self.anchor_col = anchor_col


class _Sheet:
    def __init__(self, name, rows, bold_rows=(), widths=(), chart=None):
        self.name = name
        self.rows = rows
        self.bold_rows = set(bold_rows)
        self.widths = widths
        self.chart = chart

    def xml(self) -> str:
        cols = ""
        if self.widths:
            cols = "<cols>%s</cols>" % "".join(
                f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
                for i, w in enumerate(self.widths)
            )
        body = []
        for r, row in enumerate(self.rows):
            style = 1 if r in self.bold_rows else 0
            cells = "".join(
                _cell(f"{_col_name(c)}{r + 1}", v, style) for c, v in enumerate(row)
            )
            body.append(f'<row r="{r + 1}">{cells}</row>')
        drawing = '<drawing r:id="rId1"/>' if self.chart else ""
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<worksheet xmlns="{SHEET_NS}" xmlns:r="{REL_NS}">{cols}'
            f'<sheetData>{"".join(body)}</sheetData>{drawing}</worksheet>'
        )


def _cache(values, numeric: bool) -> str:
    tag = "c:numCache" if numeric else "c:strCache"
    points = "".join(
        f'<c:pt idx="{i}"><c:v>{v if numeric else escape(str(v))}</c:v></c:pt>'
        for i, v in enumerate(values)
    )
    return f'<{tag}><c:ptCount val="{len(values)}"/>{points}</{tag}>'


def _series_xml(index, name, colour, sheet_name, chart, value_col, labels, values) -> str:
    def span(col: str) -> str:
        return f"'{sheet_name}'!${col}${chart.first_row}:${col}${chart.last_row}"

    header = f"'{sheet_name}'!${value_col}${chart.first_row - 1}"
    return (
        "<c:ser>"
        f'<c:idx val="{index}"/><c:order val="{index}"/>'
        f"<c:tx><c:strRef><c:f>{header}</c:f>"
        f'<c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>{escape(name)}</c:v>'
        "</c:pt></c:strCache></c:strRef></c:tx>"
        f'<c:spPr><a:solidFill><a:srgbClr val="{colour}"/></a:solidFill></c:spPr>'
        '<c:invertIfNegative val="0"/>'
        f"<c:cat><c:strRef><c:f>{span(chart.label_col)}</c:f>{_cache(labels, False)}"
        "</c:strRef></c:cat>"
        f"<c:val><c:numRef><c:f>{span(value_col)}</c:f>{_cache(values, True)}"
        "</c:numRef></c:val>"
        "</c:ser>"
    )


def _chart_xml(sheet: _Sheet) -> str:
    chart = sheet.chart
    label_idx = ord(chart.label_col) - 65
    in_idx = ord(chart.in_col) - 65
    out_idx = ord(chart.out_col) - 65
    data = sheet.rows[chart.first_row - 1 : chart.last_row]
    labels = [str(r[label_idx]) for r in data]
    in_values = [int(r[in_idx] or 0) for r in data]
    out_values = [int(r[out_idx] or 0) for r in data]

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<c:chartSpace xmlns:c="{CHART_NS}" xmlns:a="{MAIN_NS}" xmlns:r="{REL_NS}">'
        "<c:chart>"
        "<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:r>"
        f"<a:t>{escape(chart.title)}</a:t></a:r></a:p></c:rich></c:tx>"
        '<c:overlay val="0"/></c:title>'
        '<c:autoTitleDeleted val="0"/>'
        "<c:plotArea><c:layout/>"
        '<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/>'
        '<c:varyColors val="0"/>'
        + _series_xml(0, "In", IN_COLOR, sheet.name, chart, chart.in_col, labels, in_values)
        + _series_xml(1, "Out", OUT_COLOR, sheet.name, chart, chart.out_col, labels, out_values)
        + '<c:gapWidth val="60"/><c:overlap val="-10"/>'
        '<c:axId val="111111111"/><c:axId val="222222222"/></c:barChart>'
        '<c:catAx><c:axId val="111111111"/>'
        '<c:scaling><c:orientation val="minMax"/></c:scaling>'
        '<c:delete val="0"/><c:axPos val="b"/>'
        '<c:crossAx val="222222222"/></c:catAx>'
        '<c:valAx><c:axId val="222222222"/>'
        '<c:scaling><c:orientation val="minMax"/></c:scaling>'
        '<c:delete val="0"/><c:axPos val="l"/><c:majorGridlines/>'
        '<c:crossAx val="111111111"/></c:valAx>'
        "</c:plotArea>"
        '<c:legend><c:legendPos val="b"/><c:overlay val="0"/></c:legend>'
        '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/>'
        "</c:chart></c:chartSpace>"
    )


def _drawing_xml(anchor_col: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<xdr:wsDr xmlns:xdr="{DRAW_NS}" xmlns:a="{MAIN_NS}">'
        "<xdr:twoCellAnchor>"
        f"<xdr:from><xdr:col>{anchor_col}</xdr:col><xdr:colOff>0</xdr:colOff>"
        "<xdr:row>1</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
        f"<xdr:to><xdr:col>{anchor_col + 12}</xdr:col><xdr:colOff>0</xdr:colOff>"
        "<xdr:row>22</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>"
        '<xdr:graphicFrame macro="">'
        '<xdr:nvGraphicFramePr><xdr:cNvPr id="2" name="Chart"/>'
        "<xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>"
        '<xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm>'
        f'<a:graphic><a:graphicData uri="{CHART_NS}">'
        f'<c:chart xmlns:c="{CHART_NS}" xmlns:r="{REL_NS}" r:id="rId1"/>'
        "</a:graphicData></a:graphic>"
        "</xdr:graphicFrame><xdr:clientData/></xdr:twoCellAnchor></xdr:wsDr>"
    )


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<styleSheet xmlns="{SHEET_NS}">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border/></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)


def _workbook(sheets: list[_Sheet]) -> bytes:
    parts: list[tuple[str, str]] = []
    chart_no = 0
    overrides = [
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.styles+xml"/>',
    ]

    for i, sheet in enumerate(sheets, start=1):
        parts.append((f"xl/worksheets/sheet{i}.xml", sheet.xml()))
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
        if not sheet.chart:
            continue
        chart_no += 1
        parts.append((
            f"xl/worksheets/_rels/sheet{i}.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{PKG_NS}"><Relationship Id="rId1" '
            f'Type="{REL_NS}/drawing" Target="../drawings/drawing{chart_no}.xml"/></Relationships>',
        ))
        parts.append((f"xl/drawings/drawing{chart_no}.xml", _drawing_xml(sheet.chart.anchor_col)))
        parts.append((
            f"xl/drawings/_rels/drawing{chart_no}.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{PKG_NS}"><Relationship Id="rId1" '
            f'Type="{REL_NS}/chart" Target="../charts/chart{chart_no}.xml"/></Relationships>',
        ))
        parts.append((f"xl/charts/chart{chart_no}.xml", _chart_xml(sheet)))
        overrides.append(
            f'<Override PartName="/xl/drawings/drawing{chart_no}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'
        )
        overrides.append(
            f'<Override PartName="/xl/charts/chart{chart_no}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
        )

    sheet_tags = "".join(
        f'<sheet name="{escape(s.name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, s in enumerate(sheets, start=1)
    )
    rel_tags = "".join(
        f'<Relationship Id="rId{i}" Type="{REL_NS}/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    )

    parts.append((
        "xl/workbook.xml",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<workbook xmlns="{SHEET_NS}" xmlns:r="{REL_NS}"><sheets>{sheet_tags}</sheets></workbook>',
    ))
    parts.append((
        "xl/_rels/workbook.xml.rels",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{PKG_NS}">{rel_tags}'
        f'<Relationship Id="rId{len(sheets) + 1}" Type="{REL_NS}/styles" '
        'Target="styles.xml"/></Relationships>',
    ))
    parts.append(("xl/styles.xml", _STYLES))
    parts.append((
        "_rels/.rels",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{PKG_NS}"><Relationship Id="rId1" '
        f'Type="{REL_NS}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    ))

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        f'{"".join(overrides)}</Types>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # the container spec wants the content types first in the archive
        z.writestr("[Content_Types].xml", content_types)
        for name, xml in parts:
            z.writestr(name, xml)
    return buf.getvalue()


def build_people_report(
    *,
    title: str,
    range_label: str,
    timezone: str,
    totals: dict,
    cameras: list[dict],
    hourly: list[dict],
    daily: list[dict],
    events: list[dict],
) -> bytes:
    """The report as an .xlsx: summary, hourly and daily bars, and the crossing log."""
    summary_rows = [
        [title],
        ["Range", range_label],
        ["Timezone", timezone],
        [],
        ["Total IN", totals["in"]],
        ["Total OUT", totals["out"]],
        ["Net (IN - OUT)", totals["in"] - totals["out"]],
        ["Crossings logged", len(events)],
        [],
        ["Camera", "In", "Out"],
        *[[c["name"], c["in"], c["out"]] for c in cameras],
    ]
    sheets = [
        _Sheet("Summary", summary_rows, bold_rows=(0, 4, 5, 6, 9), widths=(24, 22, 12, 12))
    ]

    hourly_rows = [
        ["Hour", "In", "Out"],
        *[[f"{h['hour']:02d}:00", h["in"], h["out"]] for h in hourly],
        ["TOTAL", sum(h["in"] for h in hourly), sum(h["out"] for h in hourly)],
    ]
    sheets.append(_Sheet(
        "Hourly",
        hourly_rows,
        bold_rows=(0, len(hourly_rows) - 1),
        widths=(10, 8, 8),
        chart=_Chart(f"Hourly - {range_label}", 2, 1 + len(hourly), "A", "B", "C", 5)
        if hourly else None,
    ))

    daily_rows = [
        ["Date", "In", "Out", "Net"],
        *[[d["date"], d["in"], d["out"], d["in"] - d["out"]] for d in daily],
        [
            "TOTAL",
            sum(d["in"] for d in daily),
            sum(d["out"] for d in daily),
            sum(d["in"] - d["out"] for d in daily),
        ],
    ]
    sheets.append(_Sheet(
        "Daily",
        daily_rows,
        bold_rows=(0, len(daily_rows) - 1),
        widths=(14, 8, 8, 8),
        chart=_Chart(f"Daily - {range_label}", 2, 1 + len(daily), "A", "B", "C", 6)
        if daily else None,
    ))

    in_count = sum(1 for e in events if e["direction"] == "IN")
    log_rows = [
        ["Time", "Direction", "Camera"],
        *[[e["time"], e["direction"], e.get("camera") or ""] for e in events],
        ["TOTAL", f"IN={in_count}", f"OUT={len(events) - in_count}"],
    ]
    sheets.append(_Sheet(
        "Log", log_rows, bold_rows=(0, len(log_rows) - 1), widths=(22, 12, 10)
    ))

    return _workbook(sheets)
