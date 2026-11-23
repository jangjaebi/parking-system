# parking-system
Raspberry Pi-based Smart Parking Reservation and Management System

라즈베리파이 기반 스마트 주차 예약·관리 시스템 (모형 주차장 A1~A5)

## 동작 흐름

```
[웹] 숫자 4자리 차량번호로 A1~A5 중 한 칸 예약          → RESERVED (예약됨)
[입구 카메라] 차 지붕 번호판 인식 → 예약 차량이면 차단봉 개방 → ENTERED (입차 완료)
[상단 카메라] 칸에 차 감지 + 번호판 대조                    → PARKED (주차 완료)
[상단 카메라] 주차했던 칸이 비면                            → 예약 자동 종료
```

- 예약 없는 칸에 차가 서면 **무단 주차**, 예약 칸에 다른 번호 차가 서면 **번호 불일치**로 표시됩니다.
- 예약 후 30분 안에 입차하지 않으면 예약이 자동 취소됩니다. (`backend/parking_routes.py`의 `RESERVATION_TIMEOUT_MINUTES`)
- 입차한 뒤에는 예약을 취소할 수 없습니다.

## 폴더 구성

| 폴더 | 내용 |
|---|---|
| `backend/` | Flask API 서버 + SQLite (`parking.db`는 처음 실행할 때 자동 생성) |
| `vision/enter_gate.py` | 입구 카메라 OCR + 서보모터 차단봉 |
| `vision/lot_monitor.py` | 상단 카메라: A1~A5 점유 감지 + 번호판 대조 |
| `vision/plate_ocr.py` | 숫자 4자리 번호판 OCR (두 카메라 공용) |
| `frontend/` | React(Vite) 예약 웹 화면 |

## 실행 방법

### 1. 백엔드 (라즈베리파이)
```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py              # http://<라즈베리파이 IP>:5000
```

### 2. 카메라
```bash
sudo apt install tesseract-ocr
pip install -r vision/requirements.txt
cd vision
python enter_gate.py       # 입구 (실제 서보 사용 시 파일 안 USE_HARDWARE_SERVO = True)
python lot_monitor.py      # 상단 (시작할 때 주차장을 비워 두세요)
```
- 처음에는 `SHOW_PREVIEW = True`로 두고 화면을 보면서 `PARKING_ROIS`(칸 좌표)를 모형에 맞추세요.
- `lot_monitor` 미리보기 창에서 `r` = 빈 주차장 기준 화면 다시 찍기, `q` = 종료
- 번호판은 **흰 종이에 검은 숫자 4자리**를 굵게 인쇄해 차 지붕에 붙이면 인식이 잘 됩니다.

### 3. 프런트엔드
```bash
cd frontend
npm install
npm run build              # 빌드 결과(dist)를 Flask가 http://<IP>:5000 에서 바로 제공
```
화면 하단 **안내 → Flask 서버**를 누르면 실제 서버와 연결됩니다. (기본은 체험 모드)

## API 요약

| 메서드 | 경로 | 호출하는 곳 |
|---|---|---|
| GET | `/api/parking` | 웹 (3초마다) |
| POST | `/api/reservations` | 웹 — `{space_id, car_number}` |
| DELETE | `/api/reservations/<space_id>` | 웹 |
| POST | `/api/gate/entry` | 입구 카메라 — `{detected_car_number, confidence}` |
| GET/POST | `/api/gate/status` | 웹(조회) / 입구 카메라(닫힘 보고, CLOSED만 허용) |
| POST | `/api/occupancy` | 상단 카메라 — `{space_id, occupied}` |
| POST | `/api/ocr` | 상단 카메라 — `{space_id, detected_car_number, confidence}` |
