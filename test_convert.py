"""Self-check for the file converters and thread safety. Run: uv run python test_convert.py"""
import io
import threading
import time
from unittest.mock import patch

from omos_mcp import server
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

# thread safety: one Drive client per thread (sharing one breaks TLS), index built once
builds = []


def _fake_build(*args, **kwargs):
    client = object()
    builds.append(client)
    return client


with patch.object(server, "OMOS_ROOT_FOLDER_ID", "x"), \
     patch.object(server, "GOOGLE_SERVICE_ACCOUNT_JSON", "{}"), \
     patch("google.oauth2.service_account.Credentials.from_service_account_info", lambda *a, **k: None), \
     patch("googleapiclient.discovery.build", _fake_build):
    def grab():
        server._svc()
        server._svc()  # must reuse this thread's client

    threads = [threading.Thread(target=grab) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
assert len(builds) == 4, f"expected 1 client per thread (4), got {len(builds)}"

calls = []


def _slow_build():
    calls.append(1)
    time.sleep(0.1)
    server._cache["built_at"] = time.time()


server._cache["built_at"] = 0
with patch.object(server, "_build_index", _slow_build):
    threads = [threading.Thread(target=server._ensure_index) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
assert len(calls) == 1, f"index built {len(calls)} times, expected 1"

# ponytail: pdf extraction not self-checked — pypdf can't author text PDFs; covered by real-drive test
print("ok")
