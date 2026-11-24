# parking-system
Raspberry Pi-based Smart Parking Reservation and Management System

라즈베리파이 기반 스마트 주차 예약·관리 시스템 (모형 주차장 A1~A5)

## 동작 흐름

```
[앱]        숫자 4자리 차량번호로 A1~A5 중 한 칸 예약             → 예약됨 (RESERVED)
[입구]      초음파 센서가 차 감지 → 입구 카메라 번호판 OCR
            → 예약 차량이면 차단봉 올림 → 초음파로 통과 확인 후 내림 (entered = 1)
[앱]        차를 대기 전에 '주차하기'                             → 주차중 (PARKING)
[상단 카메라] 칸에 차가 있는지만 감지 (번호판은 읽지 않음)
[앱]        차를 다 댄 뒤 '주차 완료' (칸에 차가 있어야 함)        → 주차 완료 (PARKED)
[앱]        나갈 때 '주차 종료'                                  → 예약 끝
```

**앱 경고** (소리 없이 앱 화면에 빨간색으로 표시)
- 예약된 칸에 '주차하기' 없이 차가 들어오면 → "무단 주차 경고", 그 차가 빠질 때까지
- '주차 완료'를 눌렀는데 그 칸이 비어 있으면 → "주차가 완료되지 않았습니다. 자리를 확인해주세요." (10초 표시, 그 칸에 차가 들어오면 바로 사라짐)

**그 밖의 규칙**
- 칸이 비어도 예약은 자동으로 끝나지 않습니다. 자리를 맞추느라 들어갔다 나와도 괜찮고, '주차 종료'를 눌러야 끝납니다.
- 예약 없는 칸에 차가 서면 앱에 "미예약 주차"로 표시됩니다.
- 예약 후 30분 안에 입차·주차 시작을 하지 않으면 예약이 자동 취소됩니다. (`RESERVATION_TIMEOUT_MINUTES`)
- 입구를 통과하기 전에만 '예약 취소'가 가능하고, 그 뒤로는 '주차 종료'를 사용합니다.
- 같은 예약으로는 입구 차단봉이 한 번만 열립니다.

## 폴더 구성

| 폴더 | 내용 |
|---|---|
| `backend/` | Flask API 서버 + SQLite (`parking.db`는 처음 실행할 때 자동 생성) |
| `vision/enter_gate.py` | 입구 카메라 OCR + 서보모터 차단봉 |
| `vision/lot_monitor.py` | 상단 카메라: A1~A5 점유 감지 |
| `vision/plate_ocr.py` | 숫자 4자리 번호판 OCR (두 카메라 공용) |
| `frontend/` | React(Vite) 예약 웹 화면 |

## 라즈베리파이 배선 (핀 번호)

코드의 핀 번호는 **BCM(GPIO) 번호**예요. 확장보드(T자 보드)에 `GPIO18`처럼 적힌 번호와 같아요.

| 부품 | 부품 쪽 핀 | 라즈베리파이 | 물리 핀 번호 |
|---|---|---|---|
| 서보모터 (차단봉) | 주황/노랑 (신호) | **GPIO18** | 12 |
| | 빨강 (전원) | 5V | 2 |
| | 갈색 (GND) | GND | 6 |
| 초음파 센서 HC-SR04 | Trig | **GPIO23** | 16 |
| | Echo | **GPIO24** (저항 분압 거쳐서) | 18 |
| | VCC | 5V | 4 |
| | GND | GND | 14 |
| 입구 카메라 | USB | USB 포트 | - |
| 상단 카메라 | USB | USB 포트 | - |

- **Echo는 5V 신호**라서 라즈베리파이(3.3V)에 바로 꽂으면 안 돼요. `Echo → 1kΩ → GPIO24`, `GPIO24 → 2kΩ → GND`로 나눠서 연결하세요.
- 5V 핀은 2번과 4번 두 개라 서보와 초음파에 하나씩 쓰면 돼요. GND는 6, 9, 14, 20, 25, 30, 34, 39번 중 아무 곳이나 써도 돼요.
- 핀을 바꾸면 서보는 `vision/gate_motor.py`의 `SERVO_PIN`, 초음파는 `vision/enter_gate.py`의 `TRIG_PIN`, `ECHO_PIN`을 바꾸세요.

### 차단봉 모터 종류
`vision/gate_motor.py`의 `SERVO_TYPE`으로 고릅니다.
- `"continuous"` (기본값): **360도 연속회전 서보**. 각도 대신 "몇 초 동안 돌리기"로 올리고 내립니다.
  위치를 기억하지 못하므로 **프로그램을 켜기 전에 차단봉을 내린 상태로** 맞춰 두세요.
- `"positional"`: 일반 0~180도 서보. 각도로 올리고 내립니다.
- 90도 올라간 뒤 내려올 때 같은 방향으로 계속 돌아 한 바퀴를 채운다면 연속회전 서보예요.

### 부품 단독 테스트
```bash
python3 hardware/servo_check.py    # 모터 종류 확인 (일반 / 360도 연속회전) → gate_motor.py 설정 자동 변경
python3 hardware/servo_test.py     # 차단봉: o 올림 / c 내림 / + - 조금씩 → gate_motor.py의 OPEN_SECONDS·CLOSE_SECONDS 맞추기
python3 hardware/ultrasonic.py     # 초음파: 거리(cm) 출력 → CAR_DISTANCE_CM 정하기
```
라즈베리파이에서 실제로 움직이려면 `enter_gate.py`의 `USE_HARDWARE_SERVO = True`, `USE_ULTRASONIC = True`로 바꾸세요.

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
| GET | `/api/parking` | 앱 (3초마다) |
| POST | `/api/reservations` | 앱 — `{space_id, car_number}` |
| DELETE | `/api/reservations/<space_id>` | 앱 — 예약 취소 (입구 통과 전) |
| POST | `/api/reservations/<space_id>/start` | 앱 — 주차하기 `{car_number}` |
| POST | `/api/reservations/<space_id>/complete` | 앱 — 주차 완료 `{car_number}` |
| POST | `/api/reservations/<space_id>/end` | 앱 — 주차 종료 `{car_number}` |
| POST | `/api/gate/entry` | 입구 카메라 — `{detected_car_number, confidence}` |
| GET/POST | `/api/gate/status` | 앱(조회) / 입구 카메라(닫힘 보고, CLOSED만 허용) |
| POST | `/api/occupancy` | 상단 카메라 — `{space_id, occupied}` |
