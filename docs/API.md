# OMOS Transcript API

API สำหรับส่ง transcript หลังประชุมเข้า Google Drive อัตโนมัติ — OMOS จะหาโฟลเดอร์ของโปรเจคให้เอง แล้วบันทึกเป็น Google Doc

```
Zoom → น้องจิก → POST /transcripts → OMOS → <Project>/Meeting Transcripts/<Doc>
```

- **Base URL:** `https://omos-mcp.onrender.com`
- **Auth:** `Authorization: Bearer <OMOS_INGEST_TOKEN>` (ขอ token จากผู้ดูแล OMOS — คนละตัวกับ token ของ MCP)
- **Content-Type:** `application/json` (UTF-8)

---

## POST /transcripts

บันทึก transcript 1 การประชุม ลงโฟลเดอร์ของโปรเจค

### Request

| field | ชนิด | บังคับ | คำอธิบาย |
|---|---|---|---|
| `project` | string | ✅ | **ชื่อโฟลเดอร์โปรเจคใน Drive แบบตรงเป๊ะ** ตัวพิมพ์เล็ก/ใหญ่และช่องว่างต้องตรง ระบบไม่เดาให้ (ดู [การหาชื่อโปรเจค](#การหาชื่อโปรเจคที่ถูกต้อง)) |
| `title` | string | ✅ | ชื่อการประชุม ใช้ตั้งชื่อไฟล์ |
| `date` | string | ✅ | วันที่ประชุม แนะนำรูปแบบ `YYYY-MM-DD` ใช้ตั้งชื่อไฟล์ |
| `transcript` | string | ✅ | เนื้อ transcript ขึ้นบรรทัดใหม่ (`\n`) แยกตามผู้พูดได้เลย ระบบคงบรรทัดให้ |
| `meeting_id` | string | ➖ | **แนะนำให้ส่งทุกครั้ง** — ใช้กันไฟล์ซ้ำเวลา retry (ดู [Idempotency](#idempotency)) |
| `translation` | string | ➖ | คำแปล จะต่อท้ายเป็นอีกหัวข้อในไฟล์เดียวกัน |

ชื่อไฟล์ที่ได้: `<date> — <title>` เช่น `2026-09-07 — Weekly sync`

**ตัวอย่าง**

```bash
curl -X POST https://omos-mcp.onrender.com/transcripts \
  -H "Authorization: Bearer $OMOS_INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project": "todolist",
    "title": "Weekly sync",
    "date": "2026-09-07",
    "meeting_id": "zoom-8891",
    "transcript": "คุณเอ: สวัสดีครับ\nคุณบี: ขอสรุป timeline",
    "translation": "A: hello\nB: timeline summary"
  }'
```

```python
import requests

resp = requests.post(
    "https://omos-mcp.onrender.com/transcripts",
    headers={"Authorization": f"Bearer {OMOS_INGEST_TOKEN}"},
    json={
        "project": "todolist",
        "title": "Weekly sync",
        "date": "2026-09-07",
        "meeting_id": "zoom-8891",
        "transcript": transcript_text,
        "translation": translated_text,
    },
    timeout=60,
)
data = resp.json()
if resp.status_code == 200:
    print(data["status"], data["link"])       # created | exists
elif data.get("retryable"):
    schedule_retry()                           # ลองใหม่ทีหลังได้
else:
    alert_team(data["error"])                  # ต้องมีคนแก้ ไม่ต้อง retry
```

### Response — สำเร็จ (200)

```json
{
  "status": "created",
  "file_id": "1NUKb4NakjTtC3URp8JMeNJ8e0duygdRufZPpYsz_rek",
  "link": "https://docs.google.com/document/d/1NUK.../edit?usp=drivesdk",
  "folder": "todolist / Meeting Transcripts"
}
```

| field | คำอธิบาย |
|---|---|
| `status` | `created` = บันทึกใหม่ · `exists` = การประชุมนี้เคยบันทึกไว้แล้ว (ไม่สร้างซ้ำ) |
| `file_id` | id ของ Google Doc |
| `link` | ลิงก์เปิดไฟล์ |
| `folder` | path ที่บันทึกจริง |

> `exists` ถือว่า **สำเร็จ** ไม่ใช่ error — ฝั่งผู้เรียกจบงานได้เลย

### Response — ไม่สำเร็จ

ทุก error ตอบรูปแบบเดียวกัน และมี **`retryable`** บอกตรงๆ ว่า retry แล้วมีโอกาสสำเร็จไหม

```json
{ "error": "Unknown project 'ABC'. It must match a top-level folder name exactly.", "retryable": false }
```

| HTTP | ความหมาย | `retryable` | ฝั่งผู้เรียกควรทำ |
|---|---|---|---|
| `400` | body ไม่ใช่ JSON / ไม่มี field ที่บังคับ / transcript เกิน 10 MB | `false` | แก้ payload |
| `401` | token ผิดหรือไม่ได้ส่งมา | `false` | ตรวจ config |
| `403` | service account ไม่มีสิทธิ์เขียนใน Drive | `false` | แจ้งผู้ดูแล OMOS ให้เพิ่มสิทธิ์ |
| `404` | ไม่มีโปรเจคชื่อนี้ / โฟลเดอร์ปลายทางหายไป | `false` | ตรวจชื่อโปรเจค แล้วส่งใหม่ |
| `409` | ชื่อโปรเจคซ้ำกันใน Drive ตัดสินใจไม่ได้ | `false` | แจ้งให้เปลี่ยนชื่อโฟลเดอร์ให้ไม่ซ้ำ |
| `429` | ชน rate limit ของ Google | `true` | รอแล้ว retry (แนะนำ exponential backoff) |
| `502` | Drive ล่มชั่วคราว / timeout | `true` | retry ได้ |
| `507` | Drive เต็มหรือปลายทางไม่ใช่ Shared Drive | `false` | **ห้าม retry** ต้องแก้ที่ Drive |

**หลักการ retry ที่แนะนำ:** ถ้า `retryable: false` → ห้าม retry ให้เก็บ payload ไว้แล้วแจ้งคน · ถ้า `retryable: true` → backoff 1m, 5m, 15m แล้วเลิก ส่งต่อให้คนดู

---

## Idempotency

ส่ง `meeting_id` เดิมกี่ครั้งก็ได้ **จะไม่เกิดไฟล์ซ้ำ** — ครั้งแรกได้ `created` ครั้งต่อไปได้ `exists` พร้อม `file_id` เดิม

- ระบบเก็บ `meeting_id` ไว้กับไฟล์ (Drive `appProperties`) แล้วค้นก่อนสร้างทุกครั้ง
- **ไฟล์เดิมจะไม่ถูกเขียนทับ** ถึงแม้ `transcript` ที่ส่งมารอบหลังจะต่างจากเดิม
- ถ้า **ไม่ส่ง** `meeting_id` ระบบจะเทียบจากชื่อไฟล์ (`<date> — <title>`) แทน ซึ่งชนกันได้ง่ายกว่า จึงควรส่งเสมอ

ถ้าต้องแก้เนื้อหาของ transcript ที่บันทึกไปแล้ว ให้คนเข้าไปแก้ใน Google Doc โดยตรง — API นี้ไม่แก้ไขและไม่ลบไฟล์ใดๆ

---

## การหาชื่อโปรเจคที่ถูกต้อง

`project` ต้องตรงกับชื่อโฟลเดอร์ชั้นบนสุดใน Drive **เป๊ะๆ** ระบบจงใจไม่เดา เพราะเดาผิดแล้วเอกสารจะไปโผล่ผิดโปรเจค

- ชื่อไม่ตรง → `404` พร้อมข้อความบอกว่าไม่พบ
- มีโฟลเดอร์ชื่อซ้ำกัน → `409` และ**ไม่เขียนอะไรเลย**
- ดูรายชื่อโปรเจคปัจจุบันได้จากผู้ดูแล OMOS หรือผ่าน MCP tool `omos_index`

---

## ข้อจำกัดและพฤติกรรมที่ควรรู้

- **ขนาด:** transcript + translation รวมกันไม่เกิน 10 MB
- **เวลา:** ปกติตอบใน 2–5 วินาที ตั้ง client timeout ไว้ที่ **60 วินาที** เผื่อ Drive ช้า
- **cold start:** ถ้า server ไม่ได้ถูกเรียกนาน request แรกอาจใช้เวลาถึง ~60 วินาที — ควรมี retry รองรับ
- **ไฟล์ที่ได้เป็น Google Doc** แปลงจาก markdown หัวข้อ/ย่อหน้าจะจัดให้อัตโนมัติ ผู้พูดแต่ละคนอยู่คนละย่อหน้า
- **ระบบสร้างไฟล์ใหม่เท่านั้น** ไม่มีการแก้ไข ทับ หรือลบไฟล์เดิมในทุกกรณี และเขียนได้เฉพาะในโฟลเดอร์ของโปรเจคที่ระบุมาเท่านั้น
- โฟลเดอร์ `Meeting Transcripts` จะถูกสร้างให้อัตโนมัติถ้ายังไม่มี

## ตรวจสอบสถานะระบบ

```bash
curl https://omos-mcp.onrender.com/healthz   # ตอบ "ok" ถ้า server ทำงานปกติ
```

ทุก request ที่เข้ามาถูก log ไว้พร้อม `project` และ `meeting_id` (ไม่เก็บเนื้อ transcript ใน log) ขอให้ผู้ดูแลช่วยดู log ได้เมื่อสงสัยว่าไฟล์หาย
