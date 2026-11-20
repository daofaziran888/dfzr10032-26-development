"""分隔符文本解析：按语法切分记录，并保留每个字段在原文中的物理坐标。

接入的正文样本经常是 CSV 风格文本，但字段里可能带引号，引号内还会出现换行。
如果按 ``splitlines()`` 逐行切，行内换行会被误当成下一条记录，字段分隔符也会
跟着错位。这里按引号状态机扫描：只有引号外的换行才是记录边界，引号内的换行
属于字段值。解析失败（例如引号未闭合）时不丢弃内容，仍返回已识别字段及其
物理行、列，供校验人员按原文核对。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 常见业务字段名，用于判断首条记录是标题行还是数据行。
KNOWN_FIELDS = [
    "运单号",
    "货单号",
    "订单号",
    "收件人",
    "寄件人",
    "地址",
    "收件地址",
    "邮编",
    "电话",
    "手机号",
    "备注",
]

DEFAULT_DELIMITER = ","
DEFAULT_QUOTE = '"'


@dataclass
class FieldSpan:
    """一个字段值的原文坐标；行、列均从 1 开始，字符偏移从 0 开始。"""

    value: str
    raw: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    char_start: int
    char_end: int
    quoted: bool = False
    error: str | None = None


@dataclass
class ParsedRecord:
    """一条数据记录，``index`` 从 1 开始，只给数据行编号。"""

    index: int
    start_line: int
    end_line: int
    start_char: int
    end_char: int
    fields: list[FieldSpan]
    error: str | None = None

    @property
    def values(self) -> list[str]:
        return [field.value for field in self.fields]


@dataclass
class DelimitedParse:
    records: list[ParsedRecord]
    header: list[str] | None
    field_names: list[str]
    physical_lines: int
    errors: list[str] = field(default_factory=list)

    @property
    def has_error(self) -> bool:
        return bool(self.errors)


def _scan(text: str, delimiter: str, quote: str) -> list[ParsedRecord]:
    """按引号与分隔符语法扫描原文，返回带坐标的记录列表。"""
    records: list[ParsedRecord] = []
    i = 0
    line = 1
    col = 1
    total = len(text)

    fields: list[FieldSpan] = []
    record_start_line = 1
    record_start_col = 1
    record_start_char = 0
    record_error: str | None = None
    record_has_content = False

    field_start_line = 1
    field_start_col = 1
    field_start_char = 0
    field_end_line = 1
    field_end_col = 1
    raw_chars: list[str] = []
    value_chars: list[str] = []
    quoted = False
    in_quotes = False
    field_error: str | None = None

    def begin_record() -> None:
        nonlocal record_start_line, record_start_col, record_start_char
        nonlocal record_error, record_has_content
        nonlocal fields
        record_start_line = line
        record_start_col = col
        record_start_char = i
        record_error = None
        record_has_content = False
        fields = []

    def begin_field() -> None:
        nonlocal field_start_line, field_start_col, field_start_char
        nonlocal field_end_line, field_end_col
        nonlocal raw_chars, value_chars, quoted, field_error
        field_start_line = line
        field_start_col = col
        field_start_char = i
        field_end_line = line
        field_end_col = col
        raw_chars = []
        value_chars = []
        quoted = False
        field_error = None

    def finish_field(char_end: int) -> None:
        nonlocal raw_chars, value_chars
        fields.append(
            FieldSpan(
                value="".join(value_chars),
                raw="".join(raw_chars),
                start_line=field_start_line,
                start_col=field_start_col,
                end_line=field_end_line,
                end_col=field_end_col,
                char_start=field_start_char,
                char_end=char_end,
                quoted=quoted,
                error=field_error,
            )
        )
        raw_chars = []
        value_chars = []

    def finish_record(char_end: int) -> None:
        nonlocal record_error, record_has_content
        if not record_has_content:
            return
        error = record_error
        field_errors = [item.error for item in fields if item.error]
        if error is None and field_errors:
            error = field_errors[0]
        end_line = max((item.end_line for item in fields), default=record_start_line)
        records.append(
            ParsedRecord(
                index=len(records) + 1,
                start_line=record_start_line,
                end_line=end_line,
                start_char=record_start_char,
                end_char=char_end,
                fields=list(fields),
                error=error,
            )
        )
        record_has_content = False

    begin_record()
    begin_field()

    while i < total:
        ch = text[i]

        if in_quotes:
            if ch == quote:
                if i + 1 < total and text[i + 1] == quote:
                    # 连续两个引号表示一个转义引号，仍属于字段内容。
                    raw_chars.extend((ch, quote))
                    value_chars.append(quote)
                    record_has_content = True
                    field_end_line, field_end_col = line, col + 1
                    i += 2
                    col += 2
                else:
                    quoted = True
                    raw_chars.append(ch)
                    field_end_line, field_end_col = line, col
                    in_quotes = False
                    record_has_content = True
                    i += 1
                    col += 1
                continue

            record_has_content = True
            if ch == "\r":
                if i + 1 < total and text[i + 1] == "\n":
                    raw_chars.extend(("\r", "\n"))
                    i += 2
                else:
                    raw_chars.append("\r")
                    i += 1
                value_chars.append("\n")
                field_end_line, field_end_col = line, col
                line += 1
                col = 1
            elif ch == "\n":
                raw_chars.append(ch)
                value_chars.append("\n")
                field_end_line, field_end_col = line, col
                i += 1
                line += 1
                col = 1
            else:
                raw_chars.append(ch)
                value_chars.append(ch)
                field_end_line, field_end_col = line, col
                i += 1
                col += 1
            continue

        # 引号外：分隔符与新行才是结构字符。
        if ch == delimiter:
            record_has_content = True
            finish_field(i)
            i += 1
            col += 1
            begin_field()
            continue

        if ch in ("\r", "\n"):
            if ch == "\r" and i + 1 < total and text[i + 1] == "\n":
                newline_len = 2
            else:
                newline_len = 1
            finish_field(i)
            finish_record(i)
            i += newline_len
            line += 1
            col = 1
            begin_record()
            begin_field()
            continue

        if ch == quote:
            if not raw_chars:
                quoted = True
                in_quotes = True
                raw_chars.append(ch)
                record_has_content = True
                i += 1
                col += 1
            else:
                # 字段中途出现引号，标准语法不支持；保留原文并标记异常。
                if field_error is None:
                    field_error = "字段内出现未转义引号"
                raw_chars.append(ch)
                value_chars.append(ch)
                field_end_line, field_end_col = line, col
                record_has_content = True
                i += 1
                col += 1
            continue

        raw_chars.append(ch)
        value_chars.append(ch)
        field_end_line, field_end_col = line, col
        record_has_content = True
        i += 1
        col += 1

    # 收尾：未闭合引号仍保留已经读到的字段与坐标。
    if in_quotes:
        field_error = "引号未闭合，字段内容按原文保留"
        record_error = "引号未闭合，字段内容按原文保留"
        # 末尾自带的换行只表示字段内容在此结束，位置停在最后一行内容上。
        if not value_chars or value_chars[-1] != "\n":
            field_end_line, field_end_col = line, max(col - 1, 1)
    if record_has_content or fields or raw_chars or value_chars:
        finish_field(i)
        finish_record(i)

    for record in records:
        for item in record.fields:
            if item.error and record.error is None:
                record.error = item.error
    return records


def _looks_like_header(record: ParsedRecord, known_fields: list[str]) -> bool:
    values = [item.value.strip() for item in record.fields if item.value.strip()]
    if not values or len(values) != len(record.fields):
        return False
    hits = sum(1 for value in values if value in known_fields)
    return hits >= max(1, (len(values) + 1) // 2)


def parse_delimited(
    text: str,
    *,
    delimiter: str = DEFAULT_DELIMITER,
    quote: str = DEFAULT_QUOTE,
    has_header: bool | None = None,
    field_names: list[str] | None = None,
    known_fields: list[str] | None = None,
) -> DelimitedParse:
    """解析分隔符文本。

    ``has_header`` 为 ``None`` 时按已知字段名自动判断；为 ``False`` 时第一条
    记录就是数据行。``field_names`` 是数据列的字段名，标题缺失时用它补位。
    """
    known = list(dict.fromkeys([*(field_names or []), *(known_fields or KNOWN_FIELDS)]))
    records = _scan(text, delimiter, quote)
    physical_lines = text.count("\n") + 1 if text else 0

    header: list[str] | None = None
    if records:
        if has_header is True:
            header = [item.value.strip() for item in records[0].fields]
            records = records[1:]
        elif has_header is None and _looks_like_header(records[0], known):
            header = [item.value.strip() for item in records[0].fields]
            records = records[1:]

    if header is not None:
        names = header
    elif field_names:
        names = list(field_names)
    else:
        width = max((len(record.fields) for record in records), default=0)
        names = [f"列{index + 1}" for index in range(width)]

    for index, record in enumerate(records, start=1):
        record.index = index

    errors = [f"第 {record.start_line} 行：{record.error}" for record in records if record.error]
    return DelimitedParse(
        records=records,
        header=header,
        field_names=names,
        physical_lines=physical_lines,
        errors=errors,
    )
