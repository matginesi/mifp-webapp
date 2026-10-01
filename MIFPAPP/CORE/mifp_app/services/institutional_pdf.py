from __future__ import annotations

import io
import re
import threading
from dataclasses import dataclass, field
from html import escape as html_escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from .exporters import MIFP_EXPORT_COLORS

MAX_PDF_HTML_CHARS = 2_000_000
MAX_PDF_HTML_NODES = 50_000
MAX_PDF_HTML_DEPTH = 100
MAX_PDF_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PDF_IMAGE_PIXELS = 40_000_000

_BLOCK_TAGS = {
    "blockquote",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "ol",
    "p",
    "pre",
    "table",
    "ul",
}
_VOID_TAGS = {"br", "hr", "img"}
_SAFE_LINK_SCHEMES = {"http", "https", "mailto"}
_SAFE_IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$")
_FONT_LOCK = threading.Lock()


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[_Node | str] = field(default_factory=list)


class _HTMLTreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.node_count = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.node_count += 1
        if self.node_count > MAX_PDF_HTML_NODES:
            raise ValueError("PDF content is too complex")
        if len(self.stack) >= MAX_PDF_HTML_DEPTH:
            raise ValueError("PDF content is nested too deeply")
        node = _Node(tag.lower(), {key.lower(): str(value or "") for key, value in attrs})
        self.stack[-1].children.append(node)
        if node.tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == normalized:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


@dataclass(frozen=True)
class _PDFFonts:
    serif: str
    serif_bold: str
    serif_italic: str
    sans: str
    sans_bold: str
    sans_italic: str
    mono: str


def _font_file(filename: str) -> Path | None:
    roots = (
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/TTF"),
        Path("/usr/share/fonts/truetype/liberation2"),
        Path("/usr/share/fonts/truetype/liberation"),
    )
    for root in roots:
        candidate = root / filename
        if candidate.is_file():
            return candidate
    return None


def _register_fonts() -> _PDFFonts:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    dejavu = {
        "MIFPSerif": "DejaVuSerif.ttf",
        "MIFPSerif-Bold": "DejaVuSerif-Bold.ttf",
        "MIFPSerif-Italic": "DejaVuSerif-Italic.ttf",
        "MIFPSerif-BoldItalic": "DejaVuSerif-BoldItalic.ttf",
        "MIFPSans": "DejaVuSans.ttf",
        "MIFPSans-Bold": "DejaVuSans-Bold.ttf",
        "MIFPSans-Oblique": "DejaVuSans-Oblique.ttf",
        "MIFPSans-BoldOblique": "DejaVuSans-BoldOblique.ttf",
        "MIFPMono": "DejaVuSansMono.ttf",
    }
    paths = {name: _font_file(filename) for name, filename in dejavu.items()}
    if not all(paths.values()):
        return _PDFFonts(
            serif="Times-Roman",
            serif_bold="Times-Bold",
            serif_italic="Times-Italic",
            sans="Helvetica",
            sans_bold="Helvetica-Bold",
            sans_italic="Helvetica-Oblique",
            mono="Courier",
        )

    with _FONT_LOCK:
        for name, path in paths.items():
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
        pdfmetrics.registerFontFamily(
            "MIFPSerif",
            normal="MIFPSerif",
            bold="MIFPSerif-Bold",
            italic="MIFPSerif-Italic",
            boldItalic="MIFPSerif-BoldItalic",
        )
        pdfmetrics.registerFontFamily(
            "MIFPSans",
            normal="MIFPSans",
            bold="MIFPSans-Bold",
            italic="MIFPSans-Oblique",
            boldItalic="MIFPSans-BoldOblique",
        )
    return _PDFFonts(
        serif="MIFPSerif",
        serif_bold="MIFPSerif-Bold",
        serif_italic="MIFPSerif-Italic",
        sans="MIFPSans",
        sans_bold="MIFPSans-Bold",
        sans_italic="MIFPSans-Oblique",
        mono="MIFPMono",
    )


def _style_map(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for declaration in value.split(";"):
        key, separator, raw = declaration.partition(":")
        if separator:
            result[key.strip().lower()] = raw.strip()
    return result


def _safe_color(value: str) -> str | None:
    normalized = value.strip()
    return normalized if _HEX_COLOR_RE.fullmatch(normalized) else None


def _positive_int(value: str, default: int, maximum: int) -> int:
    try:
        return max(1, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _font_size(value: str) -> float | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(pt|px)?\s*", value, re.I)
    if not match:
        return None
    unit = (match.group(2) or "pt").lower()
    size = float(match.group(1)) * (0.75 if unit == "px" else 1)
    return max(6, min(24, size))


def _safe_link(href: str, source_url: str) -> str | None:
    value = href.strip()
    if not value:
        return None
    absolute = urljoin(f"{source_url.rstrip('/')}/", value)
    return absolute if urlsplit(absolute).scheme.lower() in _SAFE_LINK_SCHEMES else None


def _text_content(node: _Node | str) -> str:
    if isinstance(node, str):
        return node
    separator = "\n" if node.tag in {"br", "p", "div", "li", "tr"} else ""
    return separator.join(_text_content(child) for child in node.children)


class _InstitutionalDocument:
    def __init__(
        self,
        *,
        source_url: str,
        assets_dir: Path | None,
        static_dir: Path | None,
        fonts: _PDFFonts,
        available_width: float,
    ) -> None:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

        self.source_url = source_url.rstrip("/")
        self.assets_dir = assets_dir.resolve() if assets_dir else None
        self.static_dir = static_dir.resolve() if static_dir else None
        self.fonts = fonts
        self.available_width = available_width
        self.red = colors.HexColor(f"#{MIFP_EXPORT_COLORS['red']}")
        self.navy = colors.HexColor(f"#{MIFP_EXPORT_COLORS['navy']}")
        self.gray_100 = colors.HexColor(f"#{MIFP_EXPORT_COLORS['gray_100']}")
        self.gray_200 = colors.HexColor(f"#{MIFP_EXPORT_COLORS['gray_200']}")
        self.gray_500 = colors.HexColor(f"#{MIFP_EXPORT_COLORS['gray_500']}")
        self.body_color = colors.HexColor("#1F2937")
        self.link_color = colors.HexColor("#175CD3")
        self.alignments = {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT}

        sample = getSampleStyleSheet()
        self.styles = {
            "body": ParagraphStyle(
                "InstitutionalBody",
                parent=sample["BodyText"],
                fontName=fonts.serif,
                fontSize=9.5,
                leading=17.3,
                textColor=self.body_color,
                spaceAfter=8,
                allowWidows=0,
                allowOrphans=0,
            ),
            "h1": ParagraphStyle(
                "InstitutionalH1",
                parent=sample["Heading1"],
                fontName=fonts.serif_bold,
                fontSize=15,
                leading=19,
                textColor=self.navy,
                spaceBefore=22,
                spaceAfter=10,
                keepWithNext=True,
            ),
            "h2": ParagraphStyle(
                "InstitutionalH2",
                parent=sample["Heading2"],
                fontName=fonts.serif_bold,
                fontSize=12,
                leading=16,
                textColor=self.navy,
                spaceBefore=16,
                spaceAfter=8,
                keepWithNext=True,
            ),
            "h3": ParagraphStyle(
                "InstitutionalH3",
                parent=sample["Heading3"],
                fontName=fonts.serif_bold,
                fontSize=10.5,
                leading=14.5,
                textColor=self.navy,
                spaceBefore=12,
                spaceAfter=6,
                keepWithNext=True,
            ),
            "h4": ParagraphStyle(
                "InstitutionalH4",
                parent=sample["Heading4"],
                fontName=fonts.serif_bold,
                fontSize=10,
                leading=14,
                textColor=self.navy,
                spaceBefore=10,
                spaceAfter=5,
                keepWithNext=True,
            ),
            "table": ParagraphStyle(
                "InstitutionalTableCell",
                parent=sample["BodyText"],
                fontName=fonts.sans,
                fontSize=8.5,
                leading=11,
                textColor=self.body_color,
            ),
            "table_header": ParagraphStyle(
                "InstitutionalTableHeader",
                parent=sample["BodyText"],
                fontName=fonts.sans_bold,
                fontSize=8.5,
                leading=11,
                textColor=self.navy,
            ),
            "code": ParagraphStyle(
                "InstitutionalCode",
                parent=sample["Code"],
                fontName=fonts.mono,
                fontSize=8.5,
                leading=12,
                textColor=self.body_color,
                backColor=self.gray_100,
                borderColor=self.gray_200,
                borderWidth=0.5,
                borderPadding=7,
                spaceBefore=5,
                spaceAfter=9,
            ),
            "image_alt": ParagraphStyle(
                "InstitutionalImageAlt",
                parent=sample["BodyText"],
                fontName=fonts.sans_italic,
                fontSize=8,
                textColor=self.gray_500,
                spaceBefore=5,
                spaceAfter=8,
            ),
        }

    def inline(self, node: _Node | str) -> str:
        if isinstance(node, str):
            return html_escape(re.sub(r"\s+", " ", node), quote=False)
        content = "".join(self.inline(child) for child in node.children)
        tag = node.tag
        if tag in {"b", "strong"}:
            return f"<b>{content}</b>"
        if tag in {"i", "em"}:
            return f"<i>{content}</i>"
        if tag == "u":
            return f"<u>{content}</u>"
        if tag == "sub":
            return f"<sub>{content}</sub>"
        if tag == "sup":
            return f"<super>{content}</super>"
        if tag == "br":
            return "<br/>"
        if tag == "code":
            return f'<font name="{self.fonts.mono}">{content}</font>'
        if tag == "a":
            href = _safe_link(node.attrs.get("href", ""), self.source_url)
            if href:
                escaped_href = html_escape(href, quote=True)
                return f'<a href="{escaped_href}" color="#175CD3"><u>{content}</u></a>'
            return content
        if tag == "img":
            return html_escape(node.attrs.get("alt") or "Image", quote=False)
        if tag in {"span", "div"}:
            css = _style_map(node.attrs.get("style", ""))
            color = _safe_color(css.get("color", ""))
            if color:
                content = f'<font color="{color}">{content}</font>'
            if css.get("font-weight", "").lower() in {"bold", "600", "700", "800", "900"}:
                content = f"<b>{content}</b>"
            if css.get("font-style", "").lower() == "italic":
                content = f"<i>{content}</i>"
            if "underline" in css.get("text-decoration", "").lower():
                content = f"<u>{content}</u>"
            return content
        return content

    def paragraph(self, node: _Node, style_name: str = "body"):
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph

        style = self.styles[style_name]
        css = _style_map(node.attrs.get("style", ""))
        alignment = css.get("text-align", "").lower()
        color = _safe_color(css.get("color", ""))
        size = _font_size(css.get("font-size", ""))
        if alignment in self.alignments or color or size:
            style = ParagraphStyle(
                f"{style.name}-{alignment or 'default'}-{color or 'default'}",
                parent=style,
                alignment=self.alignments.get(alignment, style.alignment),
                textColor=color or style.textColor,
                fontSize=size or style.fontSize,
                leading=max((size or style.fontSize) * 1.4, style.leading if not size else 0),
            )
        content = "".join(self.inline(child) for child in node.children).strip()
        if css.get("font-weight", "").lower() in {"bold", "600", "700", "800", "900"}:
            content = f"<b>{content}</b>"
        if css.get("font-style", "").lower() == "italic":
            content = f"<i>{content}</i>"
        if "underline" in css.get("text-decoration", "").lower():
            content = f"<u>{content}</u>"
        return Paragraph(content or "&#160;", style)

    def _local_image_path(self, src: str) -> Path | None:
        parsed = urlsplit(urljoin(f"{self.source_url}/", src.strip()))
        source = urlsplit(self.source_url)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != source.netloc:
            return None
        mappings = (
            ("/media/", self.assets_dir),
            ("/static/", self.static_dir),
        )
        for prefix, root in mappings:
            if root is None or not parsed.path.startswith(prefix):
                continue
            relative = parsed.path.removeprefix(prefix)
            try:
                candidate = (root / relative).resolve()
                candidate.relative_to(root)
            except (OSError, ValueError):
                return None
            if (
                candidate.is_file()
                and candidate.suffix.lower() in _SAFE_IMAGE_SUFFIXES
                and candidate.stat().st_size <= MAX_PDF_IMAGE_BYTES
            ):
                return candidate
        return None

    def image(self, node: _Node) -> list[Any]:
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Image, Paragraph, Spacer

        path = self._local_image_path(node.attrs.get("src", ""))
        alt = node.attrs.get("alt") or "Image"
        if path is None:
            return [Paragraph(f"[{html_escape(alt, quote=False)}]", self.styles["image_alt"])]
        payload = io.BytesIO(path.read_bytes())
        reader = ImageReader(payload)
        pixel_width, pixel_height = reader.getSize()
        if (
            pixel_width <= 0
            or pixel_height <= 0
            or pixel_width * pixel_height > MAX_PDF_IMAGE_PIXELS
        ):
            return [Paragraph(f"[{html_escape(alt, quote=False)}]", self.styles["image_alt"])]
        width = min(self.available_width, pixel_width * 0.75)
        height = width * pixel_height / pixel_width
        max_height = 190 * 2.834645669
        if height > max_height:
            scale = max_height / height
            width *= scale
            height *= scale
        payload.seek(0)
        flowable = Image(payload, width=width, height=height)
        flowable.hAlign = "LEFT"
        return [Spacer(1, 2), flowable, Spacer(1, 10)]

    def list_flowable(self, node: _Node):
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import ListFlowable, ListItem, Paragraph

        items = []
        item_style = ParagraphStyle(
            "InstitutionalListItem",
            parent=self.styles["body"],
            leading=15.5,
            spaceAfter=2,
        )
        for child in node.children:
            if not isinstance(child, _Node) or child.tag != "li":
                continue
            inline_nodes: list[_Node | str] = []
            nested: list[Any] = []
            for grandchild in child.children:
                if isinstance(grandchild, _Node) and grandchild.tag in {"ul", "ol"}:
                    nested.append(self.list_flowable(grandchild))
                else:
                    inline_nodes.append(grandchild)
            content = "".join(self.inline(part) for part in inline_nodes).strip()
            flowables: list[Any] = [Paragraph(content or "&#160;", item_style)]
            flowables.extend(nested)
            items.append(ListItem(flowables, leftIndent=8, value=None))
        options = {
            "bulletType": "1" if node.tag == "ol" else "bullet",
            "leftIndent": 20,
            "bulletFontName": self.fonts.serif,
            "bulletFontSize": 10,
            "spaceAfter": 8,
        }
        if node.tag == "ol":
            options["start"] = "1"
        else:
            options["bulletChar"] = "•"
        return ListFlowable(items, **options)

    def blockquote(self, node: _Node):
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph, Table, TableStyle

        style = ParagraphStyle(
            "InstitutionalBlockquote",
            parent=self.styles["body"],
            fontName=self.fonts.serif_italic,
            textColor=self.gray_500,
            leftIndent=6,
            rightIndent=3,
            spaceAfter=0,
        )
        content = "".join(self.inline(child) for child in node.children).strip()
        table = Table([[Paragraph(content or "&#160;", style)]], colWidths=[self.available_width])
        table.setStyle(
            TableStyle(
                [
                    ("LINEBEFORE", (0, 0), (0, -1), 3, self.red),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ]
            )
        )
        return table

    def _table_rows(self, node: _Node) -> list[tuple[_Node, bool]]:
        rows: list[tuple[_Node, bool]] = []

        def visit(current: _Node, in_head: bool = False) -> None:
            head = in_head or current.tag == "thead"
            if current.tag == "tr":
                rows.append((current, head))
                return
            for child in current.children:
                if isinstance(child, _Node):
                    visit(child, head)

        visit(node)
        return rows

    def table(self, node: _Node):
        from reportlab.lib import colors
        from reportlab.platypus import LongTable, Paragraph, TableStyle

        source_rows = self._table_rows(node)
        if not source_rows:
            return self.paragraph(_Node("p", children=node.children))
        estimated_columns = max(
            1,
            *(
                sum(
                    _positive_int(cell.attrs.get("colspan", "1"), 1, 100)
                    for cell in row.children
                    if isinstance(cell, _Node) and cell.tag in {"td", "th"}
                )
                for row, _is_head in source_rows
            ),
        )
        grid: list[list[Any]] = [[""] * estimated_columns for _ in source_rows]
        occupied: set[tuple[int, int]] = set()
        spans: list[tuple[str, tuple[int, int], tuple[int, int]]] = []
        header_cells: list[tuple[int, int]] = []
        styled_cells: list[tuple[int, int, dict[str, str]]] = []
        repeat_rows = 0

        for row_index, (row, in_head) in enumerate(source_rows):
            column = 0
            cells = [
                child
                for child in row.children
                if isinstance(child, _Node) and child.tag in {"td", "th"}
            ]
            if (in_head or (cells and all(cell.tag == "th" for cell in cells))) and row_index == repeat_rows:
                repeat_rows += 1
            for cell in cells:
                while (row_index, column) in occupied:
                    column += 1
                colspan = min(
                    estimated_columns - column,
                    _positive_int(cell.attrs.get("colspan", "1"), 1, estimated_columns),
                )
                rowspan = min(
                    len(source_rows) - row_index,
                    _positive_int(cell.attrs.get("rowspan", "1"), 1, len(source_rows)),
                )
                style = self.styles["table_header" if cell.tag == "th" or in_head else "table"]
                content = "".join(self.inline(child) for child in cell.children).strip()
                grid[row_index][column] = Paragraph(content or "&#160;", style)
                if cell.tag == "th" or in_head:
                    header_cells.append((column, row_index))
                styled_cells.append((column, row_index, _style_map(cell.attrs.get("style", ""))))
                if colspan > 1 or rowspan > 1:
                    spans.append(("SPAN", (column, row_index), (column + colspan - 1, row_index + rowspan - 1)))
                for covered_row in range(row_index, row_index + rowspan):
                    for covered_column in range(column, column + colspan):
                        if covered_row != row_index or covered_column != column:
                            occupied.add((covered_row, covered_column))
                column += colspan

        plain_lengths = []
        for column in range(estimated_columns):
            longest = 6
            for row, _is_head in source_rows:
                cells = [
                    child
                    for child in row.children
                    if isinstance(child, _Node) and child.tag in {"td", "th"}
                ]
                if column < len(cells):
                    longest = max(longest, min(60, len(re.sub(r"\s+", " ", _text_content(cells[column])))))
            plain_lengths.append(longest)
        total_length = sum(plain_lengths) or 1
        minimum = min(18 * 2.834645669, self.available_width / estimated_columns)
        distributable = max(0, self.available_width - minimum * estimated_columns)
        widths = [minimum + distributable * length / total_length for length in plain_lengths]

        commands: list[tuple[Any, ...]] = [
            ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#D1D5DB")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        for row_index in range(len(grid)):
            if row_index % 2:
                commands.append(("BACKGROUND", (0, row_index), (-1, row_index), self.gray_100))
        for column, row_index in header_cells:
            commands.append(("BACKGROUND", (column, row_index), (column, row_index), self.gray_100))
        for column, row_index, css in styled_cells:
            background = _safe_color(css.get("background-color", ""))
            foreground = _safe_color(css.get("color", ""))
            alignment = css.get("text-align", "").upper()
            if background:
                commands.append(("BACKGROUND", (column, row_index), (column, row_index), background))
            if foreground:
                commands.append(("TEXTCOLOR", (column, row_index), (column, row_index), foreground))
            if alignment in {"LEFT", "CENTER", "RIGHT"}:
                commands.append(("ALIGN", (column, row_index), (column, row_index), alignment))
        commands.extend(spans)
        table = LongTable(
            grid,
            colWidths=widths,
            repeatRows=repeat_rows,
            splitByRow=1,
            hAlign="LEFT",
            spaceBefore=12,
            spaceAfter=12,
        )
        table.setStyle(TableStyle(commands))
        return table

    def flowables(self, nodes: list[_Node | str]) -> list[Any]:
        from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, Spacer

        result: list[Any] = []
        pending_text: list[str] = []

        def flush_text() -> None:
            content = "".join(pending_text).strip()
            pending_text.clear()
            if content:
                result.append(Paragraph(html_escape(content, quote=False), self.styles["body"]))

        for node in nodes:
            if isinstance(node, str):
                pending_text.append(node)
                continue
            flush_text()
            if node.tag in {"h1", "h2", "h3"}:
                result.append(self.paragraph(node, node.tag))
                if node.tag == "h1":
                    result.append(
                        HRFlowable(
                            width="100%",
                            thickness=1,
                            color=self.gray_200,
                            spaceBefore=-7,
                            spaceAfter=7,
                        )
                    )
            elif node.tag in {"h4", "h5", "h6"}:
                result.append(self.paragraph(node, "h4"))
            elif node.tag == "p":
                if any(isinstance(child, _Node) and child.tag == "img" for child in node.children):
                    inline_group: list[_Node | str] = []
                    for child in node.children:
                        if isinstance(child, _Node) and child.tag == "img":
                            if inline_group:
                                result.append(self.paragraph(_Node("p", children=inline_group)))
                                inline_group = []
                            result.extend(self.image(child))
                        else:
                            inline_group.append(child)
                    if inline_group:
                        result.append(self.paragraph(_Node("p", children=inline_group)))
                else:
                    result.append(self.paragraph(node))
            elif node.tag == "div":
                if any(isinstance(child, _Node) and child.tag in _BLOCK_TAGS for child in node.children):
                    result.extend(self.flowables(node.children))
                else:
                    result.append(self.paragraph(node))
            elif node.tag in {"ul", "ol"}:
                list_block = self.list_flowable(node)
                if (
                    result
                    and isinstance(result[-1], Paragraph)
                    and getattr(result[-1].style, "keepWithNext", False)
                ):
                    heading = result.pop()
                    result.append(KeepTogether([heading, list_block]))
                else:
                    result.append(KeepTogether([list_block]))
            elif node.tag == "blockquote":
                result.extend([Spacer(1, 4), self.blockquote(node), Spacer(1, 8)])
            elif node.tag == "pre":
                text = html_escape(_text_content(node), quote=False).replace(" ", "&#160;").replace("\n", "<br/>")
                result.append(Paragraph(text or "&#160;", self.styles["code"]))
            elif node.tag == "hr":
                result.append(HRFlowable(width="100%", thickness=1, color=self.gray_200, spaceBefore=8, spaceAfter=8))
            elif node.tag == "table":
                for child in node.children:
                    if isinstance(child, _Node) and child.tag == "caption":
                        result.append(self.paragraph(child, "h4"))
                result.append(self.table(node))
            elif node.tag == "img":
                result.extend(self.image(node))
            else:
                if node.children:
                    result.extend(self.flowables(node.children))
        flush_text()
        return result


def render_institutional_pdf(
    *,
    title: str,
    content_html: str,
    exported_on: str,
    source_url: str,
    assets_dir: Path | str | None = None,
    static_dir: Path | str | None = None,
) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

    if len(content_html) > MAX_PDF_HTML_CHARS:
        raise ValueError("PDF content is too large")
    parser = _HTMLTreeParser()
    parser.feed(content_html)
    parser.close()

    fonts = _register_fonts()
    output = io.BytesIO()
    available_width = A4[0] - 5.6 * cm
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=2.8 * cm,
        rightMargin=2.8 * cm,
        topMargin=2.2 * cm,
        bottomMargin=2.5 * cm,
        title=f"{title} — MIFP",
        author="Matteo Ginesi",
    )
    renderer = _InstitutionalDocument(
        source_url=source_url,
        assets_dir=Path(assets_dir) if assets_dir else None,
        static_dir=Path(static_dir) if static_dir else None,
        fonts=fonts,
        available_width=available_width,
    )
    sample = getSampleStyleSheet()
    brand = ParagraphStyle(
        "InstitutionalBrand",
        parent=sample["Heading1"],
        fontName=fonts.serif_bold,
        fontSize=15,
        leading=18,
        textColor=renderer.red,
        spaceAfter=1,
    )
    strapline = ParagraphStyle(
        "InstitutionalStrapline",
        parent=sample["Normal"],
        fontName=fonts.sans,
        fontSize=6.5,
        leading=9,
        textColor=colors.HexColor("#9CA3AF"),
        spaceAfter=8,
    )
    document_title = ParagraphStyle(
        "InstitutionalDocumentTitle",
        parent=sample["Heading1"],
        fontName=fonts.serif_bold,
        fontSize=18,
        leading=22,
        textColor=renderer.navy,
        spaceAfter=4,
    )
    meta = ParagraphStyle(
        "InstitutionalMeta",
        parent=sample["Normal"],
        fontName=fonts.sans,
        fontSize=7,
        leading=10,
        textColor=colors.HexColor("#9CA3AF"),
        spaceAfter=20,
    )
    final_footer = ParagraphStyle(
        "InstitutionalFinalFooter",
        parent=sample["Normal"],
        fontName=fonts.sans,
        fontSize=6.5,
        leading=9,
        alignment=1,
        textColor=colors.HexColor("#9CA3AF"),
        spaceBefore=8,
    )

    safe_title = html_escape(title, quote=False)
    safe_source = html_escape(source_url.rstrip("/"), quote=False)
    story: list[Any] = [
        Paragraph("MIFP", brand),
        Paragraph("MEDITERRANEAN INSTITUTE OF FUNDAMENTAL PHYSICS", strapline),
        Paragraph(safe_title, document_title),
        Paragraph(f"Exported: {html_escape(exported_on)} · Source: {safe_source}", meta),
        HRFlowable(width="100%", thickness=2.5, color=renderer.red, spaceAfter=20),
    ]
    story.extend(renderer.flowables(parser.root.children))
    story.extend(
        [
            Spacer(1, 22),
            HRFlowable(width="100%", thickness=1, color=renderer.gray_200, spaceAfter=0),
            Paragraph(f"Mediterranean Institute of Fundamental Physics — {safe_source}", final_footer),
        ]
    )

    def page_number(canvas, _document) -> None:
        canvas.saveState()
        canvas.setFont(fonts.sans, 8)
        canvas.setFillColor(colors.HexColor("#9CA3AF"))
        canvas.drawCentredString(A4[0] / 2, 1.2 * cm, str(canvas.getPageNumber()))
        canvas.restoreState()

    document.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return output.getvalue()
