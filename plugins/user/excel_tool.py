"""
excel_tool.py - Excel 文件工具集外部插件

基于 openpyxl 封装四类 LLM 可调用工具：

- excel_create  : 创建 .xlsx（多 sheet / 列类型 / 数据行）
- excel_inspect : 查看文件结构（sheet 名 / 行列数 / 表头）
- excel_read    : 读取数据（列筛选 / 分页，返回 dict 列表）
- excel_modify  : 修改数据（append_rows / add_formula / add_chart）

所有 Excel 文件操作统一封装在 ExcelDocument 类中：
创建、写入、读取、查询、追加、公式、图表、保存。

路径约定（resolve_path）：
- 绝对路径            → 原样使用
- 含目录的相对路径     → 相对项目根解析
- 纯文件名（无目录）   → 落在配置 server.output_dir 目录下
- 无 .xlsx 扩展名      → 自动补全（创建时；读取/修改时自动尝试补全查找）

返回值遵循插件约定：JSON 字符串，必须包含 success 字段。
"""

import os
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, PieChart, ScatterChart
from openpyxl.chart.reference import Reference
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from HelixCore.tools.base import BaseTool
from modules.config.config_manager import ConfigManager
from modules.utils.logger import log_tool_call
from modules.utils.paths import project_path

# 列名 → 数字格式映射（写入单元格时应用）
_NUMBER_FORMATS: Dict[str, str] = {
    "integer": "#,##0",
    "float": "0.00",
    "currency": "#,##0.00",
    "percent": "0.0%",
    "date": "yyyy-mm-dd",
    "datetime": "yyyy-mm-dd hh:mm",
}

# 日期字符串可接受的解析格式（按顺序尝试）
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
)

_TRUE_STRINGS = {"true", "1", "yes", "y", "是", "真"}

_READ_LIMIT_DEFAULT = 50
_READ_LIMIT_MAX = 1000

# 公式中裸列名的匹配模式：Unicode 字母/下划线开头，后跟字母/数字/下划线/点
_IDENT_RE = re.compile(r"[^\W\d]\w*")
_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")


def _clean(value: Any) -> Any:
    """去除字符串两侧空白；None 原样返回。"""
    if isinstance(value, str):
        return value.strip()
    return value


def _parse_datetime(value: Any) -> datetime:
    """把字符串 / datetime / date 统一解析为 datetime。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        text = value.strip()
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    raise ValueError(f"无法解析为日期: {value!r}")


class ExcelDocument:
    """对单个 .xlsx 文件的所有读写操作封装。

    用法：
        doc = ExcelDocument("/path/to/file.xlsx")   # 已存在则加载，否则新建空工作簿
        doc.create_sheet(...)
        doc.save()
    """

    def __init__(self, path: str, overwrite: bool = False):
        self.path = path
        if os.path.exists(path) and not overwrite:
            # data_only=False：公式单元格以公式字符串呈现（openpyxl 不计算值）
            self._wb = load_workbook(path, data_only=False)
        else:
            self._wb = Workbook()

    # ------------------------------------------------------------------ #
    # 路径解析
    # ------------------------------------------------------------------ #
    @staticmethod
    def resolve_path(file: str) -> str:
        """统一解析文件路径：绝对路径原样；含目录相对项目根；纯文件名 → output_dir。"""
        file = os.path.expanduser(file or "")
        if not file:
            raise ValueError("文件路径不能为空")
        if os.path.isabs(file):
            return file
        if os.path.dirname(file):
            return project_path(file)
        output_dir = project_path(ConfigManager().get_output_dir())
        return os.path.join(output_dir, file)

    @staticmethod
    def resolve_existing(file: str) -> str:
        """解析已存在文件路径；无 .xlsx 扩展名时自动尝试补全后查找。"""
        path = ExcelDocument.resolve_path(file)
        if not os.path.exists(path) and not str(file).lower().endswith(".xlsx"):
            alt = ExcelDocument.resolve_path(str(file) + ".xlsx")
            if os.path.exists(alt):
                return alt
        return path

    # ------------------------------------------------------------------ #
    # 内部工具方法
    # ------------------------------------------------------------------ #
    @staticmethod
    def _coerce_value(value: Any, col_type: str = "text") -> Any:
        """按列类型转换单元格值（创建/追加时调用）。"""
        t = (col_type or "text").strip().lower()
        if t == "text":
            return value if isinstance(value, str) else str(value)
        if t == "integer":
            return int(value)
        if t in ("float", "currency"):
            return float(value)
        if t == "percent":
            return float(value) / 100.0
        if t == "boolean":
            if isinstance(value, str):
                return value.strip().lower() in _TRUE_STRINGS
            return bool(value)
        if t in ("date", "datetime"):
            return _parse_datetime(value)
        raise ValueError(f"未知列类型: {col_type!r}")

    @staticmethod
    def _used_range(ws) -> Tuple[int, int]:
        """返回 (行数, 列数)，忽略尾部全空行/列；空表返回 (0, 0)。"""
        if ws.max_row == 1 and ws.max_column == 1 and ws.cell(1, 1).value in (None, ""):
            return (0, 0)
        max_r, max_c = ws.max_row, ws.max_column
        while max_r > 1 and all(
            ws.cell(max_r, c).value in (None, "") for c in range(1, max_c + 1)
        ):
            max_r -= 1
        while max_c > 1 and all(
            ws.cell(r, max_c).value in (None, "") for r in range(1, max_r + 1)
        ):
            max_c -= 1
        return max_r, max_c

    def _get_sheet(self, name: Optional[str] = None):
        """按名称取工作表；name 缺省取第一个含数据的表。"""
        if not name:
            for ws in self._wb.worksheets:
                if self._used_range(ws)[0] > 0:
                    return ws
            raise ValueError("工作簿中没有含数据的工作表")
        if name not in self._wb.sheetnames:
            raise ValueError(f"工作表 '{name}' 不存在，可用表: {self._wb.sheetnames}")
        return self._wb[name]

    def _headers(self, ws, ncols: int) -> List[str]:
        """读取表头行（前 ncols 列），空白单元格置空串。"""
        return [
            str(_clean(ws.cell(1, c).value)) if ws.cell(1, c).value not in (None, "") else ""
            for c in range(1, ncols + 1)
        ]

    def _resolve_column(self, ws, column: str, ncols: int, headers: List[str]) -> int:
        """按名称或字母解析列位置（1-based）。名称优先匹配表头，匹配不到的单字母视为列字母。"""
        col = _clean(str(column))
        if col in headers:
            return headers.index(col) + 1
        if len(col) <= 2 and col.isascii() and col.isalpha():
            from openpyxl.utils import column_index_from_string
            return column_index_from_string(col.upper())
        raise ValueError(f"找不到列 '{col}'，可用表头: {headers}")

    @staticmethod
    def _format_cell_value(value: Any) -> Any:
        """单元格值 → JSON 可序列化形式（日期转字符串、公式原样保留）。"""
        if isinstance(value, datetime):
            if value.hour or value.minute or value.second:
                return value.strftime("%Y-%m-%d %H:%M:%S")
            return value.strftime("%Y-%m-%d")
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, str):
            return value.strip()
        return value

    @staticmethod
    def _autofit_columns(ws, ncols: int, nrows: int) -> None:
        """按内容估算列宽（扫描全列，限制在 8~40 之间）。"""
        for c in range(1, ncols + 1):
            width = 0
            for r in range(1, nrows + 1):
                v = ws.cell(r, c).value
                if v is None:
                    continue
                length = len(str(v))
                if isinstance(v, (datetime, date)):
                    length = len("yyyy-mm-dd hh:mm")
                if length > width:
                    width = length
            ws.column_dimensions[get_column_letter(c)].width = min(40, max(8, width + 2))

    # ------------------------------------------------------------------ #
    # 创建
    # ------------------------------------------------------------------ #
    def create_sheet(
        self,
        name: str,
        columns: List[Dict[str, Any]],
        rows: Optional[List[List[Any]]] = None,
    ) -> str:
        """新建工作表：表头加粗 / 冻结首行 / 按类型写数据 / 列宽自适应。"""
        name = str(name).strip()
        if not name:
            raise ValueError("工作表名称不能为空")
        if name in self._wb.sheetnames:
            raise ValueError(f"工作表 '{name}' 已存在")
        if not columns:
            raise ValueError(f"工作表 '{name}' 的 columns 不能为空")

        # 首个创建的 sheet 复用 Workbook 默认的空白 "Sheet"
        if len(self._wb.sheetnames) == 1 and self._wb.sheetnames[0] == "Sheet" and self._wb["Sheet"].max_row == 1 and self._wb["Sheet"].max_column == 1:
            ws = self._wb["Sheet"]
            ws.title = name
        else:
            ws = self._wb.create_sheet(name)

        headers = [str(_clean(c.get("name", ""))) for c in columns]
        col_types = [str(c.get("type", "text")) for c in columns]
        if any(not h for h in headers):
            raise ValueError(f"工作表 '{name}' 存在空表头名")

        for c, header in enumerate(headers, start=1):
            cell = ws.cell(1, c, header)
            cell.font = Font(bold=True)
        ws.freeze_panes = "A2"

        rows = rows or []
        for r, row in enumerate(rows, start=2):
            if len(row) != len(columns):
                raise ValueError(
                    f"工作表 '{name}' 第 {r - 1} 行数据列数({len(row)})与表头列数({len(columns)})不一致"
                )
            for c in range(1, len(columns) + 1):
                value = row[c - 1]
                if value is None:
                    continue
                try:
                    value = self._coerce_value(value, col_types[c - 1])
                except (TypeError, ValueError) as e:
                    raise ValueError(
                        f"工作表 '{name}' 第 {r - 1} 行第 {c} 列(表头 '{headers[c - 1]}')转换失败: {e}"
                    ) from e
                cell = ws.cell(r, c, value)
                fmt = _NUMBER_FORMATS.get(col_types[c - 1])
                if fmt:
                    cell.number_format = fmt
                if isinstance(value, bool):
                    cell.number_format = "General"

        self._autofit_columns(ws, len(columns), max(1, len(rows) + 1))
        return name

    def save(self) -> str:
        """保存到 self.path（自动创建目录），返回绝对路径。"""
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._wb.save(self.path)
        return os.path.abspath(self.path)

    # ------------------------------------------------------------------ #
    # 查看
    # ------------------------------------------------------------------ #
    def inspect(self) -> List[Dict[str, Any]]:
        """返回全部工作表的结构信息。"""
        result = []
        for ws in self._wb.worksheets:
            nrows, ncols = self._used_range(ws)
            headers = self._headers(ws, ncols) if ncols else []
            result.append({
                "name": ws.title,
                "rows": nrows,          # 含表头
                "columns": ncols,
                "headers": headers,
            })
        return result

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def read(
        self,
        sheet: Optional[str] = None,
        columns: Optional[List[str]] = None,
        limit: int = _READ_LIMIT_DEFAULT,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """读取数据：按需列 + 分页，返回 {表头: 值} 的列表。"""
        ws = self._get_sheet(sheet)
        nrows, ncols = self._used_range(ws)
        headers = self._headers(ws, ncols) if ncols else []
        has_header = nrows > 0 and ncols > 0
        keys = headers if has_header else [get_column_letter(c) for c in range(1, ncols + 1)]

        cols_to_read: List[int]
        if columns:
            cols_to_read = []
            for col in columns:
                col = _clean(str(col))
                if col in headers:
                    cols_to_read.append(headers.index(col) + 1)
                else:
                    raise ValueError(f"找不到列 '{col}'，可用表头: {headers}")
        else:
            cols_to_read = list(range(1, ncols + 1))

        data_rows = max(0, nrows - 1)  # 不含表头
        limit = max(1, min(limit, _READ_LIMIT_MAX))
        offset = max(0, offset)
        start = offset + 1  # 数据区第一行为第 1 条
        end = min(start + limit, data_rows + 1)

        data = []
        for r in range(start, end):
            row_vals = [ws.cell(r + 1, c).value for c in cols_to_read]
            data.append({
                keys[c - 1]: self._format_cell_value(_clean(row_vals[i]))
                for i, c in enumerate(cols_to_read)
            })

        selected_columns = [keys[c - 1] for c in cols_to_read]
        return {
            "sheet": ws.title,
            "columns": selected_columns,
            "row_count": data_rows,
            "returned": len(data),
            "start_row": start,
            "data": data,
        }

    # ------------------------------------------------------------------ #
    # 追加行
    # ------------------------------------------------------------------ #
    @staticmethod
    def _maybe_coerce_date(value: Any) -> Any:
        """追加行时无列类型信息：仅把可识别的日期字符串转为 datetime，其他值原样保留。"""
        if isinstance(value, str):
            text = value.strip()
            if len(text) == 10 or len(text) == 19:
                for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
                    try:
                        return datetime.strptime(text, fmt)
                    except ValueError:
                        continue
        return value

    def append_rows(
        self,
        sheet: str,
        rows: List[Any],
        start_row: Optional[int] = None,
    ) -> Dict[str, Any]:
        """追加数据行。支持 list-of-lists（按列序）或 list-of-dicts（按表头匹配）。"""
        if not rows:
            raise ValueError("rows 不能为空")
        ws = self._get_sheet(sheet)
        nrows, ncols = self._used_range(ws)
        headers = self._headers(ws, ncols) if ncols else []

        insert_row = start_row if start_row is not None else nrows + 1
        if insert_row < 1:
            raise ValueError("start_row 必须 >= 1")
        first_insert_row = insert_row

        appended = 0
        for row in rows:
            if isinstance(row, dict):
                if not headers:
                    raise ValueError(
                        f"工作表 '{sheet}' 为空，无法按表头匹配 dict 形式的数据行"
                    )
                for key in row:
                    if key not in headers:
                        raise ValueError(f"找不到列 '{key}'，可用表头: {headers}")
                for c in range(1, ncols + 1):
                    value = row.get(headers[c - 1])
                    if value is not None:
                        ws.cell(insert_row, c, self._maybe_coerce_date(value))
            else:
                values = list(row)
                if ncols and len(values) > ncols:
                    raise ValueError(
                        f"第 {appended + 1} 行数据列数({len(values)})超过表头列数({ncols})"
                    )
                for c, value in enumerate(values, start=1):
                    if value is not None:
                        ws.cell(insert_row, c, self._maybe_coerce_date(value))
            insert_row += 1
            appended += 1

        return {"rows_appended": appended, "start_row": first_insert_row}

    # ------------------------------------------------------------------ #
    # 公式
    # ------------------------------------------------------------------ #
    @staticmethod
    def _translate_formula(formula: str, header_to_col: Dict[str, str], row: int) -> str:
        """把公式中的列名（裸名或 {占位符}）翻译成指定行的单元格引用。

        规则：
        - {名称} 占位符：无条件翻译，找不到列名则报错
        - 裸列名：仅当与表头完全一致，且前后不是 $ / ( / 数字 / 字母时翻译
          （避免误伤 SUM(...)、$C$1、E2 等标准 Excel 引用）
        """
        text = formula

        def replace_placeholder(match):
            name = match.group(1).strip()
            if name not in header_to_col:
                raise ValueError(f"公式占位符 {{{name}}} 找不到对应列，可用表头: {list(header_to_col)}")
            return f"{header_to_col[name]}{row}"

        text = _PLACEHOLDER_RE.sub(replace_placeholder, text)

        def replace_bare(match):
            token = match.group(0)
            start, end = match.span()
            prev_char = text[start - 1] if start > 0 else ""
            next_char = text[end] if end < len(text) else ""
            if token not in header_to_col:
                return token
            if prev_char == "$" or next_char in ("(", "$") or next_char.isdigit():
                return token
            return f"{header_to_col[token]}{row}"

        return _IDENT_RE.sub(replace_bare, text)

    def add_formula(
        self,
        sheet: str,
        column: str,
        formula: str,
        rows: Optional[str] = None,
        start_row: Optional[int] = None,
        end_row: Optional[int] = None,
    ) -> Dict[str, Any]:
        """在指定列写入公式；裸列名按行翻译为单元格引用（如 '=金额-成本' → '=E2-F2'）。"""
        formula = str(formula).strip()
        if not formula:
            raise ValueError("formula 不能为空")
        if not formula.startswith("="):
            formula = "=" + formula

        ws = self._get_sheet(sheet)
        nrows, ncols = self._used_range(ws)
        headers = self._headers(ws, ncols) if ncols else []

        col = _clean(str(column))
        from openpyxl.utils import column_index_from_string
        if col in headers:
            col_idx = headers.index(col) + 1
        elif len(col) <= 2 and col.isascii() and col.isalpha():
            col_idx = column_index_from_string(col.upper())
            existing = ws.cell(1, col_idx).value
            if col_idx <= ncols and existing not in (None, ""):
                raise ValueError(
                    f"列字母 '{col}' 对应已有表头 '{existing}'，如需写入该列请直接传列名 '{existing}'"
                )
            if existing in (None, ""):
                ws.cell(1, col_idx, col).font = Font(bold=True)
        else:
            col_idx = ncols + 1
            for c in range(1, ws.max_column + 2):
                if ws.cell(1, c).value in (None, ""):
                    col_idx = c
                    break
            ws.cell(1, col_idx, col).font = Font(bold=True)

        header_to_col = {
            h: get_column_letter(i + 1) for i, h in enumerate(headers) if h
        }
        col_letter = get_column_letter(col_idx)

        if nrows < 2:
            return {"column": col, "column_letter": col_letter, "rows_affected": 0}

        first_data, last_data = 2, nrows
        if start_row is not None:
            first_data = max(2, int(start_row))
        if end_row is not None:
            last_data = min(nrows, int(end_row))
        if rows:
            text = str(rows).strip()
            if ":" in text:
                a, b = text.split(":", 1)
                first_data = max(2, int(a))
                last_data = min(nrows, int(b))
            else:
                first_data = max(2, int(text))
                last_data = min(nrows, int(text))

        count = 0
        if first_data <= last_data:
            for r in range(first_data, last_data + 1):
                ws.cell(r, col_idx, self._translate_formula(formula, header_to_col, r))
                count += 1

        return {"column": col, "column_letter": col_letter, "rows_affected": count}

    # ------------------------------------------------------------------ #
    # 图表
    # ------------------------------------------------------------------ #
    def add_chart(
        self,
        sheet: str,
        chart_type: str,
        title: str,
        category: str,
        value: str,
        data_sheet: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按 category/value 列数据生成图表；目标 sheet 不存在自动创建。"""
        chart_type = str(chart_type).strip().lower()
        if chart_type == "column":
            chart_type = "bar"
        if chart_type not in ("bar", "line", "pie", "scatter"):
            raise ValueError(f"不支持的图表类型 '{chart_type}'，可选: bar/column/line/pie/scatter")

        if sheet in self._wb.sheetnames:
            target = self._wb[sheet]
            sheet_created = False
        else:
            target = self._wb.create_sheet(sheet)
            sheet_created = True

        # 数据来源 sheet：显式指定，否则在所有表中找同时含 category/value 列的表
        source = None
        if data_sheet:
            source = self._get_sheet(data_sheet)
        else:
            for ws in self._wb.worksheets:
                nrows, ncols = self._used_range(ws)
                headers = self._headers(ws, ncols) if ncols else []
                if category in headers and value in headers:
                    source = ws
                    break
            if source is None:
                raise ValueError(
                    f"找不到同时包含 category '{category}' 与 value '{value}' 列的工作表，可用 data_sheet 参数指定"
                )

        nrows, ncols = self._used_range(source)
        headers = self._headers(source, ncols) if ncols else []
        if nrows < 2:
            raise ValueError(f"工作表 '{source.title}' 没有数据行，无法生成图表")
        cat_idx = self._resolve_column(source, category, ncols, headers)
        val_idx = self._resolve_column(source, value, ncols, headers)
        cat_letter = get_column_letter(cat_idx)
        val_letter = get_column_letter(val_idx)

        if chart_type == "bar":
            chart = BarChart()
            chart.type = "col"  # 纵向柱状
        elif chart_type == "line":
            chart = LineChart()
        elif chart_type == "pie":
            chart = PieChart()
        else:
            chart = ScatterChart()
            chart.style = 13

        chart.title = str(title)
        data_ref = Reference(source, min_col=val_idx, min_row=1, max_row=nrows)
        chart.add_data(data_ref, titles_from_data=True)
        if chart_type == "scatter":
            chart.series[0].xvalues = Reference(
                source, min_col=cat_idx, min_row=2, max_row=nrows
            )
        else:
            chart.set_categories(
                Reference(source, min_col=cat_idx, min_row=2, max_row=nrows)
            )

        chart.anchor = "A1"
        # openpyxl 3.1 未标注 Worksheet.add_chart 类型，chart 锚点已在上面设置
        target.add_chart(chart)
        return {
            "chart_type": chart_type,
            "title": str(title),
            "category": f"{source.title}!{cat_letter}",
            "value": f"{source.title}!{val_letter}",
            "anchor": f"{target.title}!A1",
            "sheet_created": sheet_created,
        }


# ---------------------------------------------------------------------- #
# 工具 1: excel_create
# ---------------------------------------------------------------------- #
class ExcelCreateTool(BaseTool):
    """创建 Excel 文件（.xlsx）。"""

    name = "excel_create"
    description = (
        "创建 Excel(.xlsx) 文件，可一次定义多个工作表(sheets)，每个表含表头(columns)与数据行(rows)。"
        "filename 为纯文件名时保存到服务配置的 output_dir 目录；默认不覆盖已存在文件，需设置 overwrite=true。"
        "示例入参: {\"filename\": \"sales.xlsx\", \"sheets\": [{\"name\": \"Sales\", "
        "\"columns\": [{\"name\": \"日期\", \"type\": \"date\"}, {\"name\": \"产品\", \"type\": \"text\"}, "
        "{\"name\": \"数量\", \"type\": \"integer\"}, {\"name\": \"金额\", \"type\": \"currency\"}], "
        "\"rows\": [[\"2026-09-01\", \"A\", 100, 12000]]}]}"
    )
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "文件名或路径，如 'sales_report.xlsx'；纯文件名保存到 output_dir",
            },
            "sheets": {
                "type": "array",
                "description": "工作表定义列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "工作表名称"},
                        "columns": {
                            "type": "array",
                            "description": "列定义，type 可选: text/integer/float/currency/percent/boolean/date/datetime",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "列名（表头）"},
                                    "type": {"type": "string", "description": "列数据类型", "default": "text"},
                                },
                                "required": ["name"],
                            },
                        },
                        "rows": {
                            "type": "array",
                            "description": "数据行，每行元素顺序与 columns 对应",
                            "items": {"type": "array"},
                        },
                    },
                    "required": ["name", "columns"],
                },
            },
            "overwrite": {
                "type": "boolean",
                "description": "文件已存在时是否覆盖，默认 false",
                "default": False,
            },
        },
        "required": ["filename", "sheets"],
    }

    def execute(self, filename: str = "", sheets: Optional[List[Dict[str, Any]]] = None, overwrite: bool = False, **kwargs) -> str:
        log_tool_call(f"excel_create(filename='{filename}', sheets={len(sheets or [])})")
        try:
            if not filename:
                return json_fail("filename 不能为空")
            if not sheets:
                return json_fail("sheets 不能为空")
            if not filename.lower().endswith(".xlsx"):
                filename = filename + ".xlsx"
            path = ExcelDocument.resolve_path(filename)
            if os.path.exists(path) and not overwrite:
                return json_fail(
                    f"文件已存在: {path}；如需覆盖请在参数中设置 overwrite=true"
                )
            doc = ExcelDocument(path, overwrite=overwrite)
            created = []
            for sheet in sheets:
                created.append(
                    doc.create_sheet(
                        sheet.get("name", ""),
                        sheet.get("columns", []),
                        sheet.get("rows"),
                    )
                )
            saved = doc.save()
            return json_ok({
                "path": saved,
                "file": os.path.basename(saved),
                "sheets_created": len(created),
                "sheets": created,
            })
        except Exception as e:
            return json_fail(f"创建 Excel 失败: {e}")


# ---------------------------------------------------------------------- #
# 工具 2: excel_inspect
# ---------------------------------------------------------------------- #
class ExcelInspectTool(BaseTool):
    """查看 Excel 文件的结构信息。"""

    name = "excel_inspect"
    description = (
        "查看 Excel(.xlsx) 文件的结构：每个工作表(sheet)的名称、行列数与表头(headers)。"
        "文件不存在时提示先创建。示例: {\"file\": \"financial.xlsx\"}"
    )
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "文件名或路径；纯文件名时从 output_dir 查找",
            }
        },
        "required": ["file"],
    }

    def execute(self, file: str = "", **kwargs) -> str:
        log_tool_call(f"excel_inspect(file='{file}')")
        try:
            path = ExcelDocument.resolve_existing(file)
            if not os.path.exists(path):
                return json_fail(f"文件不存在: {path}，请先用 excel_create 创建")
            doc = ExcelDocument(path)
            return json_ok({
                "file": os.path.basename(path),
                "path": path,
                "sheets": doc.inspect(),
            })
        except Exception as e:
            return json_fail(f"查看 Excel 失败: {e}")


# ---------------------------------------------------------------------- #
# 工具 3: excel_read
# ---------------------------------------------------------------------- #
class ExcelReadTool(BaseTool):
    """读取 Excel 文件数据。"""

    name = "excel_read"
    description = (
        "读取 Excel(.xlsx) 文件指定工作表的数据，返回 [{表头: 值}] 列表；可筛选列(columns)与分页(limit/offset)。"
        "sheet 缺省取第一个有数据的表；日期返回 'YYYY-MM-DD' 字符串；公式单元格返回公式字符串。"
        "示例: {\"file\": \"sales.xlsx\", \"sheet\": \"Sales\", \"columns\": [\"日期\", \"金额\"], \"limit\": 100}"
    )
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "文件名或路径；纯文件名时从 output_dir 查找",
            },
            "sheet": {
                "type": "string",
                "description": "工作表名称，缺省取第一个有数据的表",
            },
            "columns": {
                "type": "array",
                "description": "要读取的列名列表，缺省读取全部列",
                "items": {"type": "string"},
            },
            "limit": {
                "type": "integer",
                "description": "最多返回行数，范围 1-1000",
                "default": 50,
            },
            "offset": {
                "type": "integer",
                "description": "跳过的数据行数，与 limit 配合分页",
                "default": 0,
            },
        },
        "required": ["file"],
    }

    def execute(
        self,
        file: str = "",
        sheet: Optional[str] = None,
        columns: Optional[List[str]] = None,
        limit: int = _READ_LIMIT_DEFAULT,
        offset: int = 0,
        **kwargs,
    ) -> str:
        log_tool_call(f"excel_read(file='{file}', sheet={sheet!r}, limit={limit}, offset={offset})")
        try:
            path = ExcelDocument.resolve_existing(file)
            if not os.path.exists(path):
                return json_fail(f"文件不存在: {path}，请先用 excel_create 创建")
            doc = ExcelDocument(path)
            result = doc.read(sheet=sheet, columns=columns, limit=limit, offset=offset)
            return json_ok({
                "file": os.path.basename(path),
                "path": path,
                **result,
            })
        except Exception as e:
            return json_fail(f"读取 Excel 失败: {e}")


# ---------------------------------------------------------------------- #
# 工具 4: excel_modify
# ---------------------------------------------------------------------- #
class ExcelModifyTool(BaseTool):
    """修改 Excel 文件（追加行 / 写公式 / 加图表）。"""

    name = "excel_modify"
    description = (
        "修改已存在的 Excel(.xlsx) 文件，operations 中多条操作按顺序执行后统一保存。"
        "支持操作: append_rows(追加数据行)、add_formula(写公式列，裸列名自动翻译为单元格引用，"
        "如 '=金额-成本' 逐行变成 '=E2-F2')、add_chart(生成图表，目标 sheet 不存在自动创建)。"
        "示例: {\"file\": \"sales.xlsx\", \"operations\": [{\"op\": \"append_rows\", \"sheet\": \"Sales\", "
        "\"rows\": [[\"2026-09-15\", \"A\", 100, 2000]]}, {\"op\": \"add_formula\", \"sheet\": \"Sales\", "
        "\"column\": \"利润\", \"formula\": \"=金额-成本\"}]}"
    )
    intents = ["*"]

    _op = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["append_rows", "add_formula", "add_chart"]},
            "sheet": {"type": "string", "description": "工作表名称"},
            "rows": {
                "oneOf": [
                    {"type": "array", "description": "append_rows: 数据行（列表或 dict 按表头匹配）"},
                    {"type": "string", "description": "add_formula: 行范围，如 '2:10'"},
                ]
            },
            "start_row": {"type": "integer", "description": "append_rows/add_formula: 起始行"},
            "end_row": {"type": "integer", "description": "add_formula: 结束行"},
            "column": {"type": "string", "description": "add_formula: 目标列名或列字母"},
            "formula": {"type": "string", "description": "add_formula: 公式，如 '=金额-成本'"},
            "type": {"type": "string", "description": "add_chart: 图表类型 bar/column/line/pie/scatter"},
            "title": {"type": "string", "description": "add_chart: 图表标题"},
            "category": {"type": "string", "description": "add_chart: 分类(X轴)列名"},
            "value": {"type": "string", "description": "add_chart: 数值(Y轴)列名"},
            "data_sheet": {"type": "string", "description": "add_chart: 数据来源表，缺省自动查找"},
        },
        "required": ["op", "sheet"],
    }
    parameters = {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "文件名或路径；纯文件名时从 output_dir 查找",
            },
            "operations": {
                "type": "array",
                "description": "按顺序执行的操作列表",
                "items": _op,
            },
        },
        "required": ["file", "operations"],
    }

    @staticmethod
    def _require_str(op: Dict[str, Any], key: str, default: str = "") -> str:
        """从操作 dict 提取必填字符串参数，缺省返回 default。"""
        value = op.get(key)
        if value is None:
            return default
        return str(value)

    @staticmethod
    def _apply_op(doc: ExcelDocument, op: Dict[str, Any]) -> Dict[str, Any]:
        op_type = ExcelModifyTool._require_str(op, "op")
        sheet = ExcelModifyTool._require_str(op, "sheet")
        if op_type == "append_rows":
            return doc.append_rows(
                sheet=sheet,
                rows=op.get("rows", []) or [],
                start_row=op.get("start_row"),
            )
        if op_type == "add_formula":
            return doc.add_formula(
                sheet=sheet,
                column=ExcelModifyTool._require_str(op, "column"),
                formula=ExcelModifyTool._require_str(op, "formula"),
                rows=op.get("rows"),
                start_row=op.get("start_row"),
                end_row=op.get("end_row"),
            )
        if op_type == "add_chart":
            return doc.add_chart(
                sheet=sheet,
                chart_type=ExcelModifyTool._require_str(op, "type"),
                title=ExcelModifyTool._require_str(op, "title", ""),
                category=ExcelModifyTool._require_str(op, "category"),
                value=ExcelModifyTool._require_str(op, "value"),
                data_sheet=op.get("data_sheet"),
            )
        raise ValueError(f"未知操作类型: {op_type!r}，可选: append_rows/add_formula/add_chart")

    def execute(self, file: str = "", operations: Optional[List[Dict[str, Any]]] = None, **kwargs) -> str:
        log_tool_call(f"excel_modify(file='{file}', operations={len(operations or [])})")
        try:
            if not file:
                return json_fail("file 不能为空")
            if not operations:
                return json_fail("operations 不能为空")
            path = ExcelDocument.resolve_existing(file)
            if not os.path.exists(path):
                return json_fail(f"文件不存在: {path}，请先用 excel_create 创建")

            doc = ExcelDocument(path)
            results = []
            for op in operations:
                result = {"op": op.get("op"), "sheet": op.get("sheet")}
                try:
                    detail = self._apply_op(doc, op)
                    result.update(detail)
                except Exception as e:
                    result["success"] = False
                    result["error"] = str(e)
                    results.append(result)
                    continue
                result["success"] = True
                results.append(result)

            saved = doc.save()
            return json_ok({
                "file": os.path.basename(path),
                "path": saved,
                "operations": results,
            })
        except Exception as e:
            return json_fail(f"修改 Excel 失败: {e}")


# ---------------------------------------------------------------------- #
# JSON 序列化辅助
# ---------------------------------------------------------------------- #
def json_ok(payload: Dict[str, Any]) -> str:
    return json_dumps({"success": True, **payload})


def json_fail(error: str) -> str:
    return json_dumps({"success": False, "error": error})


def json_dumps(payload: Dict[str, Any]) -> str:
    import json
    return json.dumps(payload, ensure_ascii=False, default=str)