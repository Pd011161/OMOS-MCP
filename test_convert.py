"""Self-check for the file converters. Run: uv run python test_convert.py"""
import io

from omos_mcp.server import _read_docx, _read_xlsx, _truncate

# docx
import docx

d = docx.Document()
d.add_paragraph("hello world")
t = d.add_table(rows=1, cols=2)
t.rows[0].cells[0].text = "a"
t.rows[0].cells[1].text = "b"
buf = io.BytesIO()
d.save(buf)
out = _read_docx(buf.getvalue())
assert "hello world" in out and "a | b" in out, out

# xlsx
import openpyxl

wb = openpyxl.Workbook()
wb.active.title = "S1"
wb.active.append(["x", 1, None])
buf = io.BytesIO()
wb.save(buf)
out = _read_xlsx(buf.getvalue())
assert "Sheet: S1" in out and "x | 1 |" in out, out

# truncate
assert _truncate("") == "(file is empty)"
assert _truncate("a" * 60_000).endswith("chars total]")

# ponytail: pdf extraction not self-checked — pypdf can't author text PDFs; covered by real-drive test
print("ok")
