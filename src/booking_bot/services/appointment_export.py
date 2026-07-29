from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from booking_bot.services.master_schedule import MasterAppointment

STATUS_LABELS = {
    "pending_approval": "Ожидает подтверждения",
    "pending_payment": "Ожидает оплаты",
    "confirmed": "Подтверждена",
    "completed": "Выполнена",
    "cancelled_by_client": "Отменена клиентом",
    "cancelled_by_master": "Отменена специалистом",
    "no_show": "Клиент не пришёл",
}

HEADERS = (
    "№",
    "Дата",
    "Начало",
    "Окончание",
    "Клиент",
    "Телефон",
    "Услуга",
    "Длительность, мин",
    "Статус",
    "Адрес",
    "Комментарий клиента",
    "Внутренняя заметка",
)


def _excel_date_serial(value: date) -> float:
    origin = datetime(1899, 12, 30)
    return (datetime.combine(value, time.min) - origin).total_seconds() / 86400


def _excel_time_serial(value: time) -> float:
    return (
        value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000
    ) / 86400


def _text_cell(reference: str, value: str | None, style: int) -> str:
    text = escape(value or "")
    return (
        f'<c r="{reference}" s="{style}" t="inlineStr">'
        f'<is><t xml:space="preserve">{text}</t></is></c>'
    )


def _number_cell(reference: str, value: int | float, style: int) -> str:
    return f'<c r="{reference}" s="{style}"><v>{value}</v></c>'


def _worksheet_xml(
    appointments: Sequence[MasterAppointment],
    *,
    specialist_name: str,
    period_start: date,
    period_end: date,
) -> str:
    title = _text_cell("A1", f"Записи специалиста {specialist_name}", 1)
    period = _text_cell(
        "A2",
        f"Период: {period_start:%d.%m.%Y}–{period_end:%d.%m.%Y}",
        2,
    )
    summary = _text_cell("A3", f"Всего записей: {len(appointments)}", 2)
    header_cells = "".join(
        _text_cell(f"{chr(65 + index)}5", header, 3)
        for index, header in enumerate(HEADERS)
    )
    rows = [
        '<row r="1" ht="28" customHeight="1">' + title + "</row>",
        '<row r="2" ht="22" customHeight="1">' + period + "</row>",
        '<row r="3" ht="22" customHeight="1">' + summary + "</row>",
        '<row r="5" ht="32" customHeight="1">' + header_cells + "</row>",
    ]
    for index, appointment in enumerate(appointments, 1):
        row_number = index + 5
        cells = [
            _number_cell(f"A{row_number}", index, 7),
            _number_cell(
                f"B{row_number}",
                _excel_date_serial(appointment.local_start.date()),
                5,
            ),
            _number_cell(
                f"C{row_number}",
                _excel_time_serial(appointment.local_start.time()),
                6,
            ),
            _number_cell(
                f"D{row_number}",
                _excel_time_serial(appointment.local_end.time()),
                6,
            ),
            _text_cell(f"E{row_number}", appointment.client_name or "Не указано", 4),
            _text_cell(f"F{row_number}", appointment.client_phone, 4),
            _text_cell(f"G{row_number}", appointment.service_name, 4),
            _number_cell(f"H{row_number}", appointment.duration_minutes, 7),
            _text_cell(
                f"I{row_number}",
                STATUS_LABELS.get(appointment.status, appointment.status),
                4,
            ),
            _text_cell(f"J{row_number}", appointment.location_name, 4),
            _text_cell(f"K{row_number}", appointment.client_comment, 8),
            _text_cell(f"L{row_number}", appointment.internal_note, 8),
        ]
        rows.append(
            f'<row r="{row_number}" ht="28" customHeight="1">' + "".join(cells) + "</row>"
        )

    last_row = max(5, len(appointments) + 5)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
        f'<dimension ref="A1:L{last_row}"/>'
        '<sheetViews><sheetView showGridLines="0" workbookViewId="0">'
        '<pane ySplit="5" topLeftCell="A6" activePane="bottomLeft" state="frozen"/>'
        '<selection pane="bottomLeft" activeCell="A6" sqref="A6"/>'
        "</sheetView></sheetViews>"
        '<sheetFormatPr defaultRowHeight="18"/>'
        "<cols>"
        '<col min="1" max="1" width="6" customWidth="1"/>'
        '<col min="2" max="2" width="13" customWidth="1"/>'
        '<col min="3" max="4" width="11" customWidth="1"/>'
        '<col min="5" max="5" width="22" customWidth="1"/>'
        '<col min="6" max="6" width="18" customWidth="1"/>'
        '<col min="7" max="7" width="28" customWidth="1"/>'
        '<col min="8" max="8" width="18" customWidth="1"/>'
        '<col min="9" max="9" width="24" customWidth="1"/>'
        '<col min="10" max="10" width="24" customWidth="1"/>'
        '<col min="11" max="12" width="32" customWidth="1"/>'
        "</cols>"
        "<sheetData>"
        + "".join(rows)
        + "</sheetData>"
        '<mergeCells count="3"><mergeCell ref="A1:L1"/><mergeCell ref="A2:L2"/>'
        '<mergeCell ref="A3:L3"/></mergeCells>'
        '<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" '
        'header="0.2" footer="0.2"/>'
        '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
        '<tableParts count="1"><tablePart r:id="rId1"/></tableParts>'
        "</worksheet>"
    )


def _styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1">
    <numFmt numFmtId="164" formatCode="hh:mm"/>
  </numFmts>
  <fonts count="4">
    <font><sz val="11"/><name val="Calibri"/><family val="2"/></font>
    <font><b/><sz val="16"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><color rgb="FF164E63"/><name val="Calibri"/></font>
  </fonts>
  <fills count="4">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid">
      <fgColor rgb="FF0F766E"/><bgColor indexed="64"/>
    </patternFill></fill>
    <fill><patternFill patternType="solid">
      <fgColor rgb="FFCCFBF1"/><bgColor indexed="64"/>
    </patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border>
      <left style="thin"><color rgb="FFD1D5DB"/></left>
      <right style="thin"><color rgb="FFD1D5DB"/></right>
      <top style="thin"><color rgb="FFD1D5DB"/></top>
      <bottom style="thin"><color rgb="FFD1D5DB"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="9">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1">
      <alignment horizontal="center" vertical="center"/>
    </xf>
    <xf numFmtId="0" fontId="3" fillId="3" borderId="0" xfId="0" applyAlignment="1">
      <alignment horizontal="left" vertical="center"/>
    </xf>
    <xf numFmtId="0" fontId="2" fillId="2" borderId="1" xfId="0" applyAlignment="1">
      <alignment horizontal="center" vertical="center" wrapText="1"/>
    </xf>
    <xf numFmtId="49" fontId="0" fillId="0" borderId="1" xfId="0"
        applyNumberFormat="1" applyAlignment="1">
      <alignment horizontal="left" vertical="center"/>
    </xf>
    <xf numFmtId="14" fontId="0" fillId="0" borderId="1" xfId="0"
        applyNumberFormat="1" applyAlignment="1">
      <alignment horizontal="center" vertical="center"/>
    </xf>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0"
        applyNumberFormat="1" applyAlignment="1">
      <alignment horizontal="center" vertical="center"/>
    </xf>
    <xf numFmtId="1" fontId="0" fillId="0" borderId="1" xfId="0"
        applyNumberFormat="1" applyAlignment="1">
      <alignment horizontal="center" vertical="center"/>
    </xf>
    <xf numFmtId="49" fontId="0" fillId="0" borderId="1" xfId="0"
        applyNumberFormat="1" applyAlignment="1">
      <alignment horizontal="left" vertical="center" wrapText="1"/>
    </xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
  <dxfs count="0"/>
  <tableStyles count="0" defaultTableStyle="TableStyleMedium2"
      defaultPivotStyle="PivotStyleLight16"/>
</styleSheet>"""


def _table_xml(row_count: int) -> str:
    last_row = max(5, row_count + 5)
    columns = "".join(
        f'<tableColumn id="{index}" name="{escape(header)}"/>'
        for index, header in enumerate(HEADERS, 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'id="1" name="AppointmentsTable" displayName="AppointmentsTable" '
        f'ref="A5:L{last_row}" totalsRowShown="0">'
        f'<autoFilter ref="A5:L{last_row}"/>'
        f'<tableColumns count="{len(HEADERS)}">{columns}</tableColumns>'
        '<tableStyleInfo name="TableStyleMedium2" showFirstColumn="0" '
        'showLastColumn="0" showRowStripes="1" showColumnStripes="0"/>'
        "</table>"
    )


def build_appointments_xlsx(
    appointments: Sequence[MasterAppointment],
    *,
    specialist_name: str,
    period_start: date,
    period_end: date,
) -> bytes:
    created_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml"
      ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml"
      ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml"
      ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/xl/tables/table1.xml"
      ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>
  <Override PartName="/docProps/core.xml"
      ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml"
      ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
      Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
      Target="xl/workbook.xml"/>
  <Relationship Id="rId2"
      Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
      Target="docProps/core.xml"/>
  <Relationship Id="rId3"
      Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties"
      Target="docProps/app.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <bookViews>
    <workbookView xWindow="0" yWindow="0" windowWidth="22000" windowHeight="12000"/>
  </bookViews>
  <sheets><sheet name="Записи" sheetId="1" r:id="rId2"/></sheets>
  <calcPr calcId="191029" fullCalcOnLoad="1"/>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
      Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
      Target="styles.xml"/>
  <Relationship Id="rId2"
      Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
      Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            _worksheet_xml(
                appointments,
                specialist_name=specialist_name,
                period_start=period_start,
                period_end=period_end,
            ),
        )
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
      Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/table"
      Target="../tables/table1.xml"/>
</Relationships>""",
        )
        archive.writestr("xl/styles.xml", _styles_xml())
        archive.writestr("xl/tables/table1.xml", _table_xml(len(appointments)))
        archive.writestr(
            "docProps/core.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Записи специалиста</dc:title>
  <dc:creator>Telegram Booking Bot</dc:creator>
  <cp:lastModifiedBy>Telegram Booking Bot</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{created_at}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{created_at}</dcterms:modified>
</cp:coreProperties>""",
        )
        archive.writestr(
            "docProps/app.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
    xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Telegram Booking Bot</Application>
  <AppVersion>1.0</AppVersion>
</Properties>""",
        )
    return output.getvalue()
