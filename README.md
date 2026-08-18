# OMOS-MCP

MCP server ให้ AI agent (Claude, ChatGPT, Cursor ฯลฯ) อ่านเอกสารโปรเจคใน **OMOS Shared Drive** เพื่อตอบคำถาม — ทุกคำตอบอ้างอิงไฟล์ต้นทางพร้อม link Google Drive

โครงสร้าง Drive ที่รองรับ:

```
OMOS/
├── Project A/
│   ├── Project Overview/
│   ├── Timeline/
│   ├── Design/
│   │   ├── System Flow/
│   │   ├── DB/
│   │   └── API/
│   └── BRD/
└── Project B/ ...
```

## Tools

| Tool | คำอธิบาย |
|------|----------|
| `omos_index` | index ทั้ง Drive: ทุกโปรเจค + ทุกไฟล์ พร้อม id และ link (agent เรียกอันนี้ก่อนเสมอ) |
| `omos_search` | ค้นหา full-text ทั้ง Drive จำกัด scope ตามโปรเจค/section ได้ |
| `omos_read` | อ่านไฟล์เป็น text — Google Docs/Sheets/Slides, PDF, .docx, .xlsx, md/text และรูปภาพ (ส่งรูปให้ agent ดูตรงๆ) |
| `omos_refresh` | rebuild index ทันที (ปกติ cache 5 นาที) |

Agent จะไล่ค้นตามลำดับ: filter **Project Name** → ไม่เจอให้ดู **Project Overview** แล้วถามยืนยันกับ user → ไล่ต่อว่าถามถึง **Timeline | BRD | Design (System Flow / DB / API)** → คำถามกว้างไปจะถามกลับเพื่อลด scope (ฝังไว้ใน server instructions แล้ว)

## ตั้งค่าครั้งเดียว: Service Account

server เข้าถึง Drive ด้วย service account (ไม่ต้องให้ทุกคน login):

1. เข้า [console.cloud.google.com](https://console.cloud.google.com) → สร้าง project ใหม่ (หรือใช้ที่มีอยู่)
2. **APIs & Services → Library** → ค้นหา **Google Drive API** → กด **Enable**
3. **IAM & Admin → Service Accounts → Create Service Account** → ตั้งชื่อ เช่น `omos-mcp` → กด Create (ข้ามขั้นตอน role ได้เลย ไม่ต้องให้สิทธิ์อะไร)
4. เข้า service account ที่สร้าง → แท็บ **Keys → Add Key → Create new key → JSON** → ไฟล์ key จะดาวน์โหลดมา
5. copy อีเมลของ service account (หน้าตา `omos-mcp@<project>.iam.gserviceaccount.com`) ไป**แชร์ Shared Drive / โฟลเดอร์ OMOS** ให้อีเมลนี้เป็น **Viewer**
6. หา **folder id** ของ root OMOS: เปิดโฟลเดอร์ใน browser แล้วดู URL `https://drive.google.com/drive/folders/<อันนี้คือ id>`

## Environment Variables

| ตัวแปร | คำอธิบาย |
|--------|----------|
| `OMOS_ROOT_FOLDER_ID` | folder id ของ root OMOS (จากขั้นตอนที่ 6) — **บังคับ** |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | เนื้อไฟล์ key JSON ทั้งก้อน หรือ path ไปยังไฟล์ — **บังคับ** |
| `OMOS_AUTH_TOKEN` | (HTTP mode) bearer token ที่ client ต้องส่งมา |
| `OMOS_INDEX_TTL` | อายุ cache ของ index เป็นวินาที (default 300) |
| `OAUTH_ISSUER` / `OAUTH_AUDIENCE` / `PUBLIC_URL` | (HTTP mode) เปิดโหมด OAuth สำหรับ Claude.ai / ChatGPT เว็บ |
| `PORT` | (HTTP mode) port ที่ฟัง (default 8000) |

## รัน local

ติดตั้ง [uv](https://docs.astral.sh/uv/) ก่อน (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

**1) ตั้งค่า `.env`** (server โหลดให้อัตโนมัติจาก root ของ repo):

```bash
cp .env.example .env
```

แก้ `.env` ใส่ `OMOS_ROOT_FOLDER_ID` และ path ไฟล์ key (เช่นวางไฟล์ key ไว้ที่ `./service-account.json` — gitignore ไว้ให้แล้ว)

**2) เชื่อมกับ Claude Code (stdio):**

```bash
claude mcp add omos -- uv run --directory "/path/to/OMOS-MCP" omos-mcp
```

Claude Desktop / Cursor / Windsurf — ใส่ใน MCP config:

```json
{
  "mcpServers": {
    "omos": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/OMOS-MCP", "omos-mcp"]
    }
  }
}
```

**3) ทดสอบ:** ถาม agent ว่า *"มีโปรเจคอะไรบ้างใน OMOS"* — ต้องได้รายชื่อโปรเจคพร้อม link

**(ทางเลือก) รันเป็น HTTP server ในเครื่อง:** เปิด `OMOS_AUTH_TOKEN` ใน `.env` แล้ว

```bash
uv run omos-mcp-http
```

endpoint อยู่ที่ `http://localhost:8000/mcp` เชื่อมด้วย:

```bash
claude mcp add --transport http omos http://localhost:8000/mcp -H "Authorization: Bearer <OMOS_AUTH_TOKEN>"
```

## Deploy เป็น MCP link (Render)

1. Push repo นี้ขึ้น GitHub (มี `Dockerfile` + `render.yaml` ให้แล้ว)
2. Render → **New → Blueprint** → เลือก repo นี้
3. ตั้ง env ใน dashboard:
   - `OMOS_ROOT_FOLDER_ID` = folder id ของ root OMOS
   - `GOOGLE_SERVICE_ACCOUNT_JSON` = เนื้อไฟล์ key JSON ทั้งก้อน (paste ตรงๆ)
   - `OMOS_AUTH_TOKEN` Render สุ่มให้เอง → ก็อปไปแจกทีม
4. Deploy เสร็จ → endpoint คือ `https://<app>.onrender.com/mcp`

ผู้ใช้เชื่อมต่อ:

```bash
claude mcp add --transport http omos https://<app>.onrender.com/mcp \
  -H "Authorization: Bearer <OMOS_AUTH_TOKEN>"
```

Cursor / VS Code: ใส่ URL + header `Authorization: Bearer <token>` ใน MCP config

### ให้ Claude.ai / ChatGPT (เว็บ) ใช้ — OAuth

เว็บ client ไม่มีช่องใส่ header ต้องใช้ OAuth ผ่าน provider เช่น **WorkOS AuthKit** (ฟรีถึง 1M users):

1. สมัคร [workos.com](https://workos.com) → เปิดใช้ AuthKit → ตั้งวิธี login + จำกัดเฉพาะคนในทีม
2. Dashboard → Connect → Configuration → เปิด **Client ID Metadata Document** และ **Dynamic Client Registration**
3. เพิ่ม **Resource Indicator** = `https://<app>.onrender.com` (ไม่มี `/mcp`)
4. ตั้ง env บน Render เพิ่ม 3 ตัว: `OAUTH_ISSUER` = AuthKit domain, `OAUTH_AUDIENCE` = `https://<app>.onrender.com`, `PUBLIC_URL` = `https://<app>.onrender.com`
5. ผู้ใช้: Claude.ai → Settings → Connectors → Add custom connector → ใส่ `https://<app>.onrender.com/mcp` → login ผ่าน WorkOS

> โหมด OAuth กับ bearer token อยู่ด้วยกันได้ — เว็บใช้ OAuth, CLI ยังใช้ token ได้

## ทดสอบ converters

```bash
uv run python test_convert.py
```
