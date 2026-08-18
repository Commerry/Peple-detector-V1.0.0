# Factory Box — People Counter

ระบบนับคนเข้า-ออกด้วยกล้องวงจรปิด (RTSP) — YOLO11n + OpenVINO (CPU, ไม่ใช้การ์ดจอ)

## การใช้งาน

| สิ่งที่ต้องการ | วิธี |
|---|---|
| เปิดระบบ | ดับเบิลคลิก `start_server.bat` แล้วเปิด http://localhost:8000 |
| จอสถานีทางเข้า | http://localhost:8000/?camera=1&kiosk=1 |
| จอสถานีทางออก | http://localhost:8000/?camera=2&kiosk=1 |
| เปิดจากเครื่องอื่นในวง LAN | เปลี่ยน localhost เป็น IP เครื่องนี้ (เปิด firewall port 8000) |
| บังคับธีมต่อจอ | เติม `&theme=dark` หรือ `&theme=light` |
| เปิดหน้าตั้งค่าตรง | เติม `?settings=1` |

## ตั้งให้รันเองตอนเปิดเครื่อง (Task Scheduler)

1. เปิด **Task Scheduler** → Create Task
2. General: ตั้งชื่อ `PeopleCounter`, เลือก **Run whether user is logged on or not** (หรือ At log on ถ้าต้องการเห็นหน้าต่าง)
3. Triggers → New → **At startup** (หน่วง 30 วินาที: Delay task for 30 seconds)
4. Actions → New → Start a program → Browse ไปที่ `d:\python\Peple-detector-web\start_server.bat`
5. Settings: ติ๊ก **If the task fails, restart every 1 minute**

## ติดตั้งบน Ubuntu (มินิพีซีหน้างาน)

เครื่องที่ใช้งานจริง: **ASUS PN41 / Celeron N4505 (2 คอร์) / RAM 8GB / Ubuntu 24.04** ที่ `10.1.100.87`

```bash
# บนเครื่อง Ubuntu หลังคัดลอกโปรเจคมาแล้ว
cd ~/people-counter
bash tools/install_ubuntu.sh
```
สคริปต์จะติดตั้ง dependency, สร้าง venv, ตั้ง systemd ให้เปิดเองตอนบูต และเปิดพอร์ต 8000

### ค่าที่ใช้จริงบนเครื่องนี้ (ผ่านการวัดแล้ว)

| ตั้งค่า | ค่า | เหตุผล |
|---|---|---|
| Processing device | **CPU** | iGPU เร็วกว่า (20 ms เทียบ 70 ms) **แต่ค้างและทำโปรแกรมตายเป็นระยะ** เมื่อต้องขับ 2 จอไปด้วย (`CL_OUT_OF_RESOURCES`, `GPU HANG`) — CPU ช้ากว่าแต่ไม่เคยล้ม |
| Inference size | 320 | 416 ช้ากว่าเกือบเท่าตัวบน CPU ตัวนี้ |
| Detect every N | 4 | ได้ ~4-5 ครั้ง/วิ ต่อกล้อง เหลือ CPU ให้เบราว์เซอร์ 2 จอ |
| Preview fps | 8 | จำกัดการวาดภาพ/บีบอัด JPEG ไม่ให้แย่ง CPU จากการนับ |

**ถ้าย้ายไปเครื่องที่มี AVX2** (เช่น Intel N100, Core i3 ขึ้นไป) ตั้ง detect_every_n กลับเป็น 2-3 และเพิ่ม imgsz เป็น 416 ได้

### จอแสดงผล 2 จอ (kiosk)

```bash
bash tools/install_kiosk.sh        # ติดตั้ง chromium + autologin + autostart
bash tools/kiosk_displays.sh       # ทดสอบเปิดเดี๋ยวนี้ ไม่ต้องรีบูต
```
จอซ้าย = กล้อง 1, จอขวา = กล้อง 2 (แก้ URL ได้ในหัวไฟล์ `tools/kiosk_displays.sh`)

GNOME ไม่จำการตั้งค่าจอที่สั่งด้วย xrandr สคริปต์จึงจัดจอใหม่ทุกครั้งที่ล็อกอิน

คำสั่งที่ใช้บ่อย:
```bash
sudo systemctl status peoplecounter     # ดูสถานะ
sudo systemctl restart peoplecounter    # รีสตาร์ท
tail -f ~/people-counter/data/server.log  # ดู log
```

## ย้ายไปติดตั้งเครื่องอื่น (เช่น มินิพีซีหน้างาน)

1. คัดลอกโฟลเดอร์ทั้งหมดไปเครื่องปลายทาง
2. ติดตั้ง Python 3.10 แล้วสร้าง venv:
   ```
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r backend\requirements.txt
   ```
3. **เช็คว่าเครื่องไหวกี่กล้อง** (วัดจริง ไม่ใช่เดา):
   ```
   .venv\Scripts\python.exe tools\check_machine.py
   ```
   จะบอกความเร็วตรวจจับของ CPU และการ์ดจอออนบอร์ด พร้อมแนะนำค่าที่ควรตั้ง
4. ดูรุ่น CPU จริงของเครื่อง:
   ```
   wmic cpu get name
   ```
5. เอาค่าที่แนะนำไปตั้งใน Settings > System (Processing device, Inference size)

### เข้าเครื่องปลายทางไม่ได้ (ping ได้แต่ ssh ไม่ได้)

```
.venv\Scripts\python.exe tools\check_remote.py <ip-เครื่องปลายทาง>
```
บอกว่าพอร์ตไหนเปิดอยู่ (SSH / Remote Desktop / เว็บแอป) และต้องทำอะไรต่อ

Windows **ไม่ได้เปิด SSH มาให้ตั้งแต่แรก** ต้องติดตั้งเองที่เครื่องนั้น (เปิด PowerShell แบบ Run as administrator):
```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

เปิดพอร์ตเว็บแอปให้เครื่องอื่นเข้าถึงได้ด้วย:
```powershell
New-NetFirewallRule -DisplayName 'People Counter' -Direction Inbound -Protocol TCP -Action Allow -LocalPort 8000
```

## โครงสร้าง

```
backend/          FastAPI + ตรวจจับ (detector, camera_worker, tracker, database)
frontend/         Vue 3 (build แล้วเสิร์ฟจาก backend อัตโนมัติ)
data/             config.json, people_counter.db (SQLite), snapshots/
start_server.bat  เปิดระบบ
tools/            check_machine.py — วัดว่าเครื่องรับได้กี่กล้อง
```

## แก้ frontend แล้ว build ใหม่

```
cd frontend
npm run build
```
(รีเฟรชหน้าเว็บ ไม่ต้อง restart server)

## ตั้งค่าที่ควรรู้

- **RTSP URL**: ใช้ substream ของกล้อง (stream2 / ความละเอียดต่ำ) — ลดโหลด CPU มาก
- **Count mode**: ประตูทางเดียวตั้ง In only / Out only → กันนับผิดทิศ 100%
- **เส้นนับ**: Settings → Edit counting line — ลากจุด 2 จุดบนภาพสด ลูกศรเขียว = ฝั่ง "เข้า" (กด Swap สลับได้)
- **Snapshots**: เปิดใน Settings → System ถ้าอยากได้ภาพหลักฐานทุกครั้งที่นับ (ลบเก่าอัตโนมัติตามจำนวนวันที่ตั้ง)
- ตัวเลขหน้าจอรีเซ็ตเที่ยงคืนอัตโนมัติ ประวัติทั้งหมดอยู่ใน Dashboard + Export CSV
