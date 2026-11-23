"""
입구 카메라 + 차단봉(서보모터) + 초음파 센서
- 초음파 센서가 차단봉 앞에 차를 감지하면 번호판 OCR 시작 (차가 없을 때는 OCR 안 함)
- 숫자 4자리 번호판을 OCR로 읽어 서버(/api/gate/entry)에 보냄
- 서버가 OPEN을 돌려주면 차단봉을 열고, 초음파 센서로 차가 지나간 것을 확인한 뒤 닫고 서버에 CLOSED 보고
- USE_ULTRASONIC = False이면 예전 방식 (항상 OCR, 열고 GATE_OPEN_SECONDS 뒤 닫기)
"""
import time

import cv2
import requests

from plate_ocr import read_4digit_plate

# 라즈베리파이 실제 연결 시 True로 변경 (PC 테스트 시 False)
USE_HARDWARE_SERVO = False
# 초음파 센서(HC-SR04) 연결 시 True로 변경
USE_ULTRASONIC = False
# 카메라 화면 미리보기 창 (번호판 영역 맞출 때 True, 모니터 없는 라즈베리파이에서는 False)
SHOW_PREVIEW = True

SERVER_URL = "http://127.0.0.1:5000"
ENTRY_CAM_INDEX = 0          # 입구 카메라 번호 (환경에 맞게 0 또는 1, 2로 조정)

# 번호판을 찾을 영역 (x1, y1, x2, y2). None이면 화면 전체
# 화면 전체를 읽으면 주변 글자(벽, 종이 등)의 숫자를 번호판으로 착각할 수 있으니
# SHOW_PREVIEW로 확인하면서 차가 멈추는 위치에 맞춰 두는 것을 권장
PLATE_ROI = None             # 예: (160, 120, 480, 360)

OCR_MIN_CONFIDENCE = 0.70    # 서버와 같은 기준
GATE_OPEN_SECONDS = 5        # 차단봉 열림 유지 시간
SAME_PLATE_COOLDOWN = 10     # 같은 번호판 재요청 간격(초)
SERVO_OPEN_ANGLE = 90
SERVO_CLOSED_ANGLE = 0

# ── 초음파 센서 설정 ──
# 센서는 차단봉 바로 옆에서 차로를 가로질러 보도록 설치 (차가 차단봉 앞에 서 있으면 감지됨)
TRIG_PIN = 23                # hardware/ultrasonic.py와 같은 핀
ECHO_PIN = 24                # ※ ECHO는 5V 출력 → 저항 분압(예: 1kΩ + 2kΩ)으로 3.3V로 낮춰 연결
CAR_DISTANCE_CM = 10         # 이 거리보다 가까우면 '차 있음' (모형 차로 폭에 맞게 조정)
MIN_OPEN_SECONDS = 2         # 열린 뒤 최소 유지 시간 (센서 순간 오측정으로 바로 닫히는 것 방지)
CLEAR_HOLD_SECONDS = 1.0     # 차가 안 보이는 상태가 이만큼 계속되면 '통과 완료'로 보고 닫음
GATE_MAX_WAIT_SECONDS = 30   # 열고 나서 차가 계속 안 지나가면 경고 출력 간격

servo = None
if USE_HARDWARE_SERVO:
    from gpiozero import AngularServo
    # 서보 떨림이 심하면 pigpio 사용 권장:
    #   sudo pigpiod 실행 후 아래 두 줄 주석 해제
    # from gpiozero.pins.pigpio import PiGPIOFactory
    # from gpiozero import Device; Device.pin_factory = PiGPIOFactory()
    servo = AngularServo(18, min_angle=0, max_angle=90, min_pulse_width=0.0005, max_pulse_width=0.0025)
    servo.angle = SERVO_CLOSED_ANGLE  # 초기 상태: 닫힘


sensor = None
if USE_ULTRASONIC:
    from gpiozero import DistanceSensor
    # queue_len: 최근 측정값 평균 → 튀는 값 완화
    sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=1, queue_len=5)


def get_distance_cm():
    """센서 거리(cm). 센서를 안 쓰면 None"""
    if sensor is None:
        return None
    return sensor.distance * 100


def car_present():
    """차단봉 앞에 차가 있는지. 센서를 안 쓰면 항상 True (= 항상 OCR)"""
    if sensor is None:
        return True
    return get_distance_cm() < CAR_DISTANCE_CM


def wait_until_car_passes():
    """차가 센서 앞을 지나가 안 보이게 될 때까지 대기 (차가 있는 동안에는 절대 닫지 않음)"""
    if sensor is None:
        time.sleep(GATE_OPEN_SECONDS)  # 센서 없음: 예전처럼 시간 기준
        return

    opened_at = time.time()
    last_warn = opened_at
    clear_since = None
    while True:
        now = time.time()
        if car_present():
            clear_since = None
        elif clear_since is None:
            clear_since = now

        if (now - opened_at >= MIN_OPEN_SECONDS and clear_since is not None
                and now - clear_since >= CLEAR_HOLD_SECONDS):
            print(f"[초음파] 차량 통과 확인 ({now - opened_at:.1f}초)")
            return

        if now - last_warn >= GATE_MAX_WAIT_SECONDS:
            print("[초음파] 차량이 아직 차단봉 앞에 있어 열림 유지 중...")
            last_warn = now
        time.sleep(0.1)


def set_servo(angle):
    if servo is not None:
        servo.angle = angle


def flush_camera(cap, count=5):
    """차단봉이 열려 있던 동안 쌓인 옛날 프레임 버리기"""
    for _ in range(count):
        cap.grab()


def open_and_auto_close_gate(cap):
    print(f">>> [서보모터] 입구 차단봉 열림 ({SERVO_OPEN_ANGLE}도)")
    set_servo(SERVO_OPEN_ANGLE)
    wait_until_car_passes()  # 초음파로 통과 확인 (센서 없으면 GATE_OPEN_SECONDS 대기)
    print(f">>> [서보모터] 입구 차단봉 닫힘 ({SERVO_CLOSED_ANGLE}도)")
    set_servo(SERVO_CLOSED_ANGLE)

    try:
        requests.post(f"{SERVER_URL}/api/gate/status", json={"status": "CLOSED"}, timeout=2)
    except requests.RequestException as e:
        print("[서버 통신 오류] 차단봉 닫힘 보고 실패:", e)

    flush_camera(cap)


def crop_roi(frame):
    if PLATE_ROI is None:
        return frame
    x1, y1, x2, y2 = PLATE_ROI
    return frame[y1:y2, x1:x2]


def draw_preview(frame, plate, conf, has_car=True):
    view = frame.copy()
    if PLATE_ROI is not None:
        x1, y1, x2, y2 = PLATE_ROI
        cv2.rectangle(view, (x1, y1), (x2, y2), (0, 200, 255), 2)
    if not has_car:
        label = "no car (sensor)"
    else:
        label = f"{plate} ({conf:.2f})" if plate else "no plate"
    cv2.putText(view, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    dist = get_distance_cm()
    if dist is not None:
        cv2.putText(view, f"{dist:.1f} cm", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 200, 0), 2)
    cv2.imshow("Entry Gate (q: quit)", view)
    return cv2.waitKey(1) & 0xFF


def main():
    cap = cv2.VideoCapture(ENTRY_CAM_INDEX)
    if not cap.isOpened():
        print(f"입구 카메라({ENTRY_CAM_INDEX}번)를 열 수 없습니다.")
        return

    print("입구 OCR 카메라 및 차단봉 시스템 가동 시작...")
    print(f"초음파 센서: {'사용 (' + str(CAR_DISTANCE_CM) + 'cm 이내 감지 시 OCR)' if sensor else '미사용 (항상 OCR)'}")
    last_checked_plate = None
    last_checked_time = 0.0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.5)
                continue

            # 프레임은 계속 읽어 카메라 버퍼를 최신으로 유지하고, OCR은 차가 있을 때만 실행
            has_car = car_present()
            if has_car:
                plate, conf = read_4digit_plate(crop_roi(frame))
            else:
                plate, conf = None, 0.0
                last_checked_plate = None  # 차가 떠나면 같은 번호도 다시 확인 가능

            if SHOW_PREVIEW and draw_preview(frame, plate, conf, has_car) == ord('q'):
                break

            if not plate or conf < OCR_MIN_CONFIDENCE:
                time.sleep(0.2)
                continue

            # 같은 번호판을 연속 중복 요청하지 않도록 쿨타임 적용
            if plate == last_checked_plate and time.time() - last_checked_time < SAME_PLATE_COOLDOWN:
                time.sleep(0.2)
                continue

            last_checked_plate = plate
            last_checked_time = time.time()
            print(f"[입구 감지] 번호판: {plate} (신뢰도: {conf:.2f})")

            try:
                res = requests.post(
                    f"{SERVER_URL}/api/gate/entry",
                    json={"detected_car_number": plate, "confidence": round(conf, 2)},
                    timeout=3
                ).json()
            except (requests.RequestException, ValueError) as e:
                print("[서버 통신 오류]", e)
                last_checked_plate = None  # 서버가 살아나면 바로 다시 시도
                continue

            print("[서버 응답]", res.get("message"))
            if res.get("gate_action") == "OPEN":
                open_and_auto_close_gate(cap)
                last_checked_time = time.time()  # 닫힌 직후 같은 차를 다시 보내지 않도록

            time.sleep(0.2)
    finally:
        cap.release()
        set_servo(SERVO_CLOSED_ANGLE)
        if sensor is not None:
            sensor.close()
        if SHOW_PREVIEW:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
