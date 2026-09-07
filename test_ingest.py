"""Self-check for transcript ingestion. Run: uv run python test_ingest.py

Covers what must never go wrong: writing into the wrong project, writing twice,
writing without a valid token, or touching an existing file.
"""
import json
import os
from unittest.mock import patch

os.environ.setdefault("OMOS_INGEST_TOKEN", "test-token")

from omos_mcp import server  # noqa: E402

ROOT = "root"
PROJECT_ID = "p-alpha"
SUB_ID = "sub-transcripts"

server.INGEST_TOKEN = "test-token"


class FakeFiles:
    """Records every Drive call so the tests can assert what was (not) touched."""

    def __init__(self, children, existing=None):
        self.children = children
        self.existing = existing or []
        self.created = []
        self.calls = []

    def list(self, **kw):
        self.calls.append(("list", kw))
        q = kw.get("q", "")
        if "appProperties" in q or "name='" in q:
            return _Exec({"files": self.existing})
        parent = q.split("'")[1] if "'" in q else ""
        return _Exec({"files": self.children.get(parent, [])})

    def create(self, **kw):
        self.calls.append(("create", kw))
        self.created.append(kw)
        name = kw["body"].get("name", "")
        new_id = SUB_ID if kw["body"].get("mimeType") == server.FOLDER_MT else f"file-{len(self.created)}"
        return _Exec({"id": new_id, "webViewLink": f"https://drive/{new_id}", "name": name})

    def update(self, **kw):
        raise AssertionError("ingestion must never update an existing file")

    def delete(self, **kw):
        raise AssertionError("ingestion must never delete a file")


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeSvc:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


def setup(children, existing=None, projects=None):
    fake = FakeFiles(children, existing)
    server._projects.update(
        built_at=9e9, by_name=projects if projects is not None else {"Alpha": [PROJECT_ID]}
    )
    return fake, patch.object(server, "_svc", lambda: FakeSvc(fake))


GOOD = {
    "project": "Alpha", "title": "Weekly sync", "date": "2026-09-03",
    "transcript": "hello there", "meeting_id": "zoom-1",
}

# --- happy path: creates the subfolder, then the doc, inside the right project ---
fake, svc = setup({PROJECT_ID: [], SUB_ID: []})
with svc:
    out = server.save_transcript(GOOD)
assert out["status"] == "created", out
assert out["folder"] == "Alpha / Meeting Transcripts", out
folder_call, doc_call = [c for c in fake.calls if c[0] == "create"]
assert folder_call[1]["body"]["parents"] == [PROJECT_ID], "subfolder must go in the project"
assert doc_call[1]["body"]["parents"] == [SUB_ID], "doc must go in the transcripts subfolder"
assert doc_call[1]["body"]["mimeType"] == server.DOC_MT, "must be a Google Doc"
assert doc_call[1]["body"]["name"] == "2026-09-03 — Weekly sync", doc_call[1]["body"]["name"]
assert doc_call[1]["body"]["appProperties"]["meeting_id"] == "zoom-1"
assert doc_call[1]["media_body"].mimetype() == "text/markdown", "upload as markdown so Drive makes real headings"

# --- an existing subfolder is reused, not created again ---
existing_sub = {"id": SUB_ID, "name": "Meeting Transcripts", "mimeType": server.FOLDER_MT}
fake, svc = setup({PROJECT_ID: [existing_sub], SUB_ID: []})
with svc:
    server.save_transcript(GOOD)
assert len([c for c in fake.calls if c[0] == "create"]) == 1, "must reuse the existing subfolder"

# --- idempotency: the same meeting filed twice creates nothing new ---
already = [{"id": "file-old", "name": "2026-09-03 — Weekly sync", "webViewLink": "https://drive/old"}]
fake, svc = setup({PROJECT_ID: [existing_sub], SUB_ID: []}, existing=already)
with svc:
    out = server.save_transcript(GOOD)
assert out["status"] == "exists" and out["file_id"] == "file-old", out
assert not fake.created, "a repeat must not create a second copy"

# --- refusals: nothing may be written ---
def refuses(payload, status, projects=None):
    fake, svc = setup({PROJECT_ID: [existing_sub], SUB_ID: []}, projects=projects)
    with svc:
        try:
            server.save_transcript(payload)
        except server.IngestError as exc:
            assert exc.status == status, f"expected {status}, got {exc.status}: {exc.message}"
            assert not fake.created, f"refused ingest ({status}) must not write anything"
            return exc.message
    raise AssertionError(f"expected IngestError {status} for {payload}")


assert "Missing required field" in refuses({**GOOD, "transcript": "  "}, 400)
assert "project" in refuses({**GOOD, "project": ""}, 400)
refuses({**GOOD, "project": "Nope"}, 404)
# duplicate project names exist in the real drive (BTS, Euca Area, SCG Experience)
msg = refuses(GOOD, 409, projects={"Alpha": [PROJECT_ID, "p-alpha-2"]})
assert "refusing to guess" in msg, msg
refuses({**GOOD, "transcript": "x" * (server.MAX_TRANSCRIPT_BYTES + 1)}, 400)

# --- the document body carries what a reader needs ---
body = server._doc_body("T", "2026-09-03", "line one", "แปลไทย", "zoom-9")
assert body.startswith("# T"), "title must be a markdown heading Drive can convert"
assert "Meeting date: 2026-09-03" in body and "Meeting ID: zoom-9" in body
assert "## Transcript" in body and "line one" in body
# speaker turns must survive as separate paragraphs, not run together
multi = server._doc_body("T", "d", "A: one\nB: two\n\nC: three", "", "")
assert "A: one\n\nB: two\n\nC: three" in multi, multi
assert "## Translation" in body and "แปลไทย" in body
assert "## Translation" not in server._doc_body("T", "d", "x", "", "")

# --- auth: the route must reject a bad token in BOTH auth modes ---
import asyncio  # noqa: E402


class FakeRequest:
    def __init__(self, token, payload=None):
        self.headers = {"authorization": f"Bearer {token}"} if token else {}
        self._payload = payload if payload is not None else GOOD

    async def json(self):
        return self._payload


def route_of(app, path):
    return next(r for r in app.router.routes if getattr(r, "path", None) == path)


def build_app(oauth: bool):
    env = {"OMOS_INGEST_TOKEN": "test-token"}
    env.update({"OAUTH_ISSUER": "https://issuer.test", "OAUTH_AUDIENCE": "https://a.test",
                "PUBLIC_URL": "https://a.test"} if oauth else {"OMOS_AUTH_TOKEN": "mcp-token"})
    captured = {}

    def fake_run(app, **kw):
        captured["app"] = app

    with patch.dict(os.environ, env, clear=False), \
         patch.object(server, "_svc", lambda: FakeSvc(FakeFiles({}))), \
         patch("uvicorn.run", fake_run), \
         patch.object(server, "_OAuthTokenVerifier", lambda *a, **k: object()):
        server.main_http()
    return captured["app"]


for oauth_mode in (True, False):
    app = build_app(oauth_mode)
    endpoint = route_of(app, "/transcripts").endpoint
    for bad in ("", "wrong-token"):
        resp = asyncio.run(endpoint(FakeRequest(bad)))
        assert resp.status_code == 401, f"oauth={oauth_mode} token={bad!r} got {resp.status_code}"
    fake, svc = setup({PROJECT_ID: [existing_sub], SUB_ID: []})
    with svc:
        resp = asyncio.run(endpoint(FakeRequest("test-token")))
    assert resp.status_code == 200, resp.status_code
    assert json.loads(resp.body)["status"] == "created"

    # a refusal reaches the caller as its own status code, so retries can be sane
    fake, svc = setup({PROJECT_ID: [existing_sub], SUB_ID: []})
    with svc:
        resp = asyncio.run(endpoint(FakeRequest("test-token", {**GOOD, "project": "Nope"})))
    assert resp.status_code == 404, resp.status_code

print("ok")
