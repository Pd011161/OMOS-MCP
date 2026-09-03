import io
import json
import os
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image


def _load_dotenv() -> None:
    # ponytail: stdlib .env loader (repo root or cwd), real env always wins
    for p in (Path(__file__).resolve().parents[2] / ".env", Path(".env")):
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
            return


_load_dotenv()

# --- Configuration (all overridable via environment) ---
OMOS_ROOT_FOLDER_ID = os.environ.get("OMOS_ROOT_FOLDER_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
INDEX_TTL = int(os.environ.get("OMOS_INDEX_TTL", "300"))  # seconds

FOLDER_MT = "application/vnd.google-apps.folder"
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 50_000

INSTRUCTIONS = (
    "OMOS shared-drive knowledge base — the single source of truth for ALL internal project "
    "documents. USE THESE TOOLS (without being asked) whenever the user asks about any project, "
    "BRD, requirement, timeline, แผนงาน, กำหนดการ, system flow, database/DB schema, API spec, "
    "project overview, รายละเอียดโปรเจค, เอกสารโปรเจค, or mentions a project by name.\n"
    "Each top-level folder is one project — the folder name IS the project name. "
    "The structure inside each project varies; rely on the index, not on a fixed layout.\n\n"
    "How to answer questions:\n"
    "1. ALWAYS call omos_index first and match the user's project against the top-level "
    "folder names.\n"
    "2. If no folder name matches, pick the most likely project(s) from the index and "
    "ASK THE USER TO CONFIRM which one they mean. If you can't guess, ask for the project name.\n"
    "3. Browse that project's subtree in the index for relevant files; use omos_search for "
    "keywords, omos_read to read a file.\n"
    "4. If the question is too broad, ask the user to narrow the scope before searching.\n"
    "5. EVERY answer must cite the source file(s) by name WITH the Google Drive link "
    "(provided in every tool response)."
)

mcp = FastMCP("omos-mcp", instructions=INSTRUCTIONS)


# --- Google Drive client ---

_drive = None


def _svc():
    global _drive
    if _drive is None:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        if not OMOS_ROOT_FOLDER_ID:
            raise RuntimeError("OMOS_ROOT_FOLDER_ID is not set.")
        raw = GOOGLE_SERVICE_ACCOUNT_JSON
        if not raw:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set (JSON content or a file path).")
        info = json.loads(raw) if raw.lstrip().startswith("{") else json.loads(
            Path(raw).expanduser().read_text(encoding="utf-8")
        )
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        _drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _drive


_FILE_FIELDS = "id,name,mimeType,webViewLink"


def _list_children(folder_id: str) -> list[dict]:
    files, token = [], None
    while True:
        resp = _svc().files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields=f"nextPageToken, files({_FILE_FIELDS})",
            pageSize=1000,
            pageToken=token,
            orderBy="folder,name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files += resp.get("files", [])
        token = resp.get("nextPageToken")
        if not token:
            return files


# --- Index cache ---
# _paths: file/folder id -> ("Project A / Design / DB / schema.pdf", webViewLink)
_cache: dict = {"built_at": 0.0, "markdown": "", "paths": {}, "projects": {}}


def _build_index() -> None:
    lines: list[str] = ["# OMOS Project Index", ""]
    paths: dict[str, tuple[str, str]] = {}
    projects: dict[str, str] = {}

    def walk(folder_id: str, prefix: str, depth: int) -> None:
        for f in _list_children(folder_id):
            path = f"{prefix} / {f['name']}" if prefix else f["name"]
            paths[f["id"]] = (path, f.get("webViewLink", ""))
            indent = "  " * depth
            if f["mimeType"] == FOLDER_MT:
                if depth == 0:
                    projects[f["name"]] = f["id"]
                    lines.append(f"\n## {f['name']}")
                else:
                    lines.append(f"{indent}- 📁 {f['name']}")
                if depth < 6:  # ponytail: depth cap, raise if the drive ever nests deeper
                    walk(f["id"], path, depth + 1)
            else:
                lines.append(
                    f"{indent}- 📄 {f['name']} — id: `{f['id']}` — [link]({f.get('webViewLink', '')})"
                )

    walk(OMOS_ROOT_FOLDER_ID, "", 0)
    _cache.update(
        built_at=time.time(),
        markdown="\n".join(lines),
        paths=paths,
        projects=projects,
    )


def _ensure_index() -> None:
    if time.time() - _cache["built_at"] > INDEX_TTL:
        _build_index()


def _cite(file_id: str, name: str, link: str) -> str:
    _ensure_index()
    path = _cache["paths"].get(file_id, (name, ""))[0]
    return f"📄 **{name}**\n📁 {path}\n🔗 {link}\n\n---\n\n"


# --- Tools ---
# All tools are async and push the blocking Google API work into a worker thread:
# FastMCP runs sync tools directly on the event loop, which froze /healthz during
# long Drive walks and made Render kill the instance mid-call.


@mcp.tool()
async def omos_index() -> str:
    """Get the full index of the OMOS drive: every project and its files (with file ids and links).
    Use this whenever the user asks about any internal project, BRD, timeline, design,
    system flow, DB, API, or เอกสารโปรเจค — ALWAYS call this first. Each top-level
    folder is one project (folder name = project name); match the user's project
    against those names, then browse that project's subtree for relevant files."""
    import anyio.to_thread

    return await anyio.to_thread.run_sync(_omos_index)


def _omos_index() -> str:
    _ensure_index()
    n = len(_cache["projects"])
    return _cache["markdown"] + f"\n\n_{n} project(s). Cite files with the links above._"


@mcp.tool()
async def omos_search(query: str, project: str = "", section: str = "") -> str:
    """Full-text search across the OMOS drive. Returns matching files with project path and link;
    use omos_read on the ids to read content.

    Args:
        query: keyword or phrase (searches file content and names)
        project: optional exact top-level folder (project) name from omos_index to narrow the search
        section: optional subfolder name inside the project to narrow further
    """
    import anyio.to_thread

    return await anyio.to_thread.run_sync(_omos_search, query, project, section)


def _omos_search(query: str, project: str, section: str) -> str:
    _ensure_index()
    if project and project not in _cache["projects"]:
        names = ", ".join(sorted(_cache["projects"])) or "(no projects found)"
        return f"Unknown project '{project}'. Available projects: {names}"

    q = query.replace("\\", "\\\\").replace("'", "\\'")
    files, token = [], None
    while True:
        resp = _svc().files().list(
            q=f"fullText contains '{q}' and trashed=false and mimeType != '{FOLDER_MT}'",
            fields=f"nextPageToken, files({_FILE_FIELDS})",
            pageSize=100,
            pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files += resp.get("files", [])
        token = resp.get("nextPageToken")
        if not token or len(files) >= 300:
            break

    # Scope by path prefix using the index (Drive queries can't filter by subtree).
    results = []
    for f in files:
        entry = _cache["paths"].get(f["id"])
        if entry is None:
            _build_index()  # new file not in cache yet
            entry = _cache["paths"].get(f["id"])
            if entry is None:
                continue  # outside the OMOS root
        path = entry[0]
        parts = [p.strip() for p in path.split(" / ")]
        if project and (not parts or parts[0] != project):
            continue
        if section and section not in parts[1:-1]:
            continue
        results.append(f"- 📄 {f['name']} — {path} — id: `{f['id']}` — [link]({f.get('webViewLink', '')})")
        if len(results) >= 20:
            break

    scope = " / ".join(x for x in (project, section) if x) or "all projects"
    if not results:
        return f"No files matching '{query}' in {scope}. Try a broader query or check omos_index."
    return f"Found {len(results)} file(s) matching '{query}' in {scope}:\n" + "\n".join(results)


@mcp.tool()
async def omos_read(file_id: str):
    """Read a file from the OMOS drive as text (Google Docs/Sheets/Slides, PDF, docx, xlsx,
    markdown/text) or as an image (png/jpg). The response starts with a citation block
    (file name, path, link) — always include that citation in your answer.

    Args:
        file_id: the Drive file id from omos_index or omos_search
    """
    import anyio.to_thread

    return await anyio.to_thread.run_sync(_omos_read, file_id)


def _omos_read(file_id: str):
    svc = _svc()
    meta = svc.files().get(
        fileId=file_id, fields="id,name,mimeType,webViewLink,size", supportsAllDrives=True
    ).execute()
    mt, name = meta["mimeType"], meta["name"]
    cite = _cite(file_id, name, meta.get("webViewLink", ""))

    if int(meta.get("size", 0)) > MAX_DOWNLOAD_BYTES:
        return cite + f"Error: file is too large to read ({meta['size']} bytes). Open it via the link."

    # Google-native files: export via Drive API
    exports = {
        "application/vnd.google-apps.document": "text/markdown",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }
    if mt in exports:
        try:
            data = svc.files().export(fileId=file_id, mimeType=exports[mt]).execute()
        except Exception:
            data = svc.files().export(fileId=file_id, mimeType="text/plain").execute()
        return cite + _truncate(data.decode("utf-8", errors="replace"))
    if mt.startswith("application/vnd.google-apps"):
        return cite + f"Error: unsupported Google file type '{mt}'. Open it via the link."

    data = svc.files().get_media(fileId=file_id, supportsAllDrives=True).execute()

    if mt.startswith("image/"):
        fmt = mt.split("/", 1)[1].split("+")[0]
        return [cite + "(image below)", Image(data=data, format=fmt)]
    if mt == "application/pdf":
        from pypdf import PdfReader

        pages = [page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages]
        return cite + _truncate("\n\n".join(pages))
    if name.lower().endswith(".docx"):
        return cite + _truncate(_read_docx(data))
    if name.lower().endswith((".xlsx", ".xlsm")):
        return cite + _truncate(_read_xlsx(data))
    if mt.startswith("text/") or mt in ("application/json", "application/xml") or name.lower().endswith(
        (".md", ".txt", ".csv", ".json", ".yaml", ".yml")
    ):
        return cite + _truncate(data.decode("utf-8", errors="replace"))

    return cite + f"Error: unsupported file type '{mt}'. Open it via the link."


@mcp.tool()
async def omos_refresh() -> str:
    """Rebuild the drive index so newly added projects/files show up immediately
    (the index is otherwise cached for a few minutes)."""
    import anyio.to_thread

    await anyio.to_thread.run_sync(_build_index)
    return f"Index rebuilt: {len(_cache['projects'])} project(s), {len(_cache['paths'])} item(s)."


# --- File converters ---


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS] + f"\n\n[... truncated, {len(text)} chars total]"
    return text or "(file is empty)"


def _read_docx(data: bytes) -> str:
    import docx

    doc = docx.Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        parts.append(
            "\n".join(" | ".join(c.text.strip() for c in row.cells) for row in table.rows)
        )
    return "\n\n".join(parts)


def _read_xlsx(data: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 200:  # ponytail: row cap per sheet, raise if real sheets are bigger
                rows.append("[... more rows truncated]")
                break
            rows.append(" | ".join("" if v is None else str(v) for v in row))
        parts.append(f"### Sheet: {ws.title}\n" + "\n".join(rows))
    return "\n\n".join(parts)


def main():
    mcp.run(transport="stdio")


# --- Remote HTTP mode (same skeleton as wiki-mcp) ---


class _OAuthTokenVerifier:
    """Validates bearer tokens issued by an external OAuth provider (WorkOS AuthKit, Auth0, …).

    Accepts either a valid provider JWT (so Claude.ai / ChatGPT web work via OAuth)
    or the shared team token (so header-capable clients keep working).
    """

    def __init__(self, issuer: str, audience: str, static_token: str = ""):
        import jwt  # provided transitively by mcp

        self._jwt = jwt
        self._issuer = issuer
        # Accept the audience with and without a trailing slash: providers and the
        # MCP resource-metadata normalization don't always agree on it.
        base = audience.rstrip("/")
        self._audience = [base, base + "/"]
        self._static = static_token
        self._jwk_client = jwt.PyJWKClient(self._discover_jwks(issuer))

    @staticmethod
    def _discover_jwks(issuer: str) -> str:
        import urllib.request

        base = issuer.rstrip("/")
        for path in ("/.well-known/openid-configuration", "/.well-known/oauth-authorization-server"):
            try:
                with urllib.request.urlopen(base + path, timeout=10) as resp:  # noqa: S310
                    return json.load(resp)["jwks_uri"]
            except Exception:
                continue
        raise RuntimeError(f"Could not discover JWKS endpoint from issuer {issuer}")

    async def verify_token(self, token: str):
        import hmac
        import logging

        from mcp.server.auth.provider import AccessToken

        log = logging.getLogger("omos_mcp.auth")

        if self._static and hmac.compare_digest(token, self._static):
            return AccessToken(token=token, client_id="team-token", scopes=[], subject="team")
        try:
            key = self._jwk_client.get_signing_key_from_jwt(token).key
            # Verify signature + audience here; check issuer manually below so a
            # trailing-slash difference doesn't cause a spurious rejection.
            claims = self._jwt.decode(
                token, key,
                algorithms=["RS256", "RS384", "RS512"],
                audience=self._audience,
                options={"verify_iss": False},
            )
        except Exception as exc:
            log.warning("omos-mcp: token rejected during decode: %s", exc)
            return None

        token_iss = (claims.get("iss") or "").rstrip("/")
        if token_iss != self._issuer.rstrip("/"):
            log.warning("omos-mcp: issuer mismatch: token=%r expected=%r", token_iss, self._issuer)
            return None

        scope = claims.get("scope", "")
        return AccessToken(
            token=token,
            client_id=claims.get("azp") or claims.get("client_id") or "oauth",
            scopes=scope.split() if isinstance(scope, str) else list(scope or []),
            subject=claims.get("sub"),
            expires_at=claims.get("exp"),
            resource=self._audience[0],
            claims=claims,
        )


def _build_http_server(**kwargs) -> FastMCP:
    """Build a FastMCP for HTTP serving with the OMOS tools registered.

    DNS-rebinding protection is disabled because the server runs behind a TLS
    reverse proxy (e.g. Render) under an external hostname and is already
    protected by auth — the default localhost-only Host allowlist would
    otherwise reject every proxied request with 421.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    server = FastMCP(
        "omos-mcp",
        instructions=INSTRUCTIONS,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        **kwargs,
    )
    for fn in (omos_index, omos_search, omos_read, omos_refresh):
        server.tool()(fn)
    return server


def main_http():
    """Run as a remote HTTP (streamable) server.

    Two auth modes (auto-selected by env):
      * OAuth — set OAUTH_ISSUER, OAUTH_AUDIENCE, PUBLIC_URL. Web clients
        (Claude.ai, ChatGPT) authenticate through the provider. The shared
        OMOS_AUTH_TOKEN, if set, still works for header-capable clients.
      * Shared bearer — set only OMOS_AUTH_TOKEN. Works with header-capable
        clients (Claude Code, Cursor, VS Code), not web OAuth clients.
    """
    import hmac

    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse, PlainTextResponse
    from starlette.routing import Route

    token = os.environ.get("OMOS_AUTH_TOKEN", "")
    issuer = os.environ.get("OAUTH_ISSUER", "")
    audience = os.environ.get("OAUTH_AUDIENCE", "")
    public_url = os.environ.get("PUBLIC_URL", "")

    _svc()  # build Drive client at startup (fails loudly on bad credentials)

    async def health(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    if issuer:
        if not (audience and public_url):
            raise SystemExit(
                "OAUTH_ISSUER is set, so OAUTH_AUDIENCE and PUBLIC_URL are required too."
            )
        from mcp.server.auth.settings import AuthSettings

        server = _build_http_server(
            token_verifier=_OAuthTokenVerifier(issuer, audience, token),
            auth=AuthSettings(
                issuer_url=issuer,
                resource_server_url=public_url,
                required_scopes=[],
            ),
        )
        app = server.streamable_http_app()
        app.router.routes.append(Route("/healthz", health, methods=["GET"]))
    else:
        if not token:
            raise SystemExit(
                "Set OMOS_AUTH_TOKEN (shared-bearer mode) or OAUTH_ISSUER + "
                "OAUTH_AUDIENCE + PUBLIC_URL (OAuth mode) so the endpoint is not public."
            )

        class BearerAuth(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                if request.url.path == "/healthz":
                    return await call_next(request)
                header = request.headers.get("authorization", "")
                provided = header[7:] if header.startswith("Bearer ") else ""
                if not hmac.compare_digest(provided, token):
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
                return await call_next(request)

        app = _build_http_server().streamable_http_app()
        app.router.routes.append(Route("/healthz", health, methods=["GET"]))
        app.add_middleware(BearerAuth)

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
