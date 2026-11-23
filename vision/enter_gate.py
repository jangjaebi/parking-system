"""
입구 카메라 + 차단봉(서보모터)
- 차 지붕에 붙인 숫자 4자리 번호판을 OCR로 읽어 서버(/api/gate/entry)에 보냄
- 서버가 OPEN을 돌려주면 차단봉을 열고, 일정 시간 뒤 닫은 후 서버에 CLOSED 보고
"""
import time

import cv2
import requests

from plate_ocr import read_4digit_plate

# 라즈베리파이 실제 연결 시 True로 변경 (PC 테스트 시 False)
USE_HARDWARE_SERVO = False
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

servo = None
if USE_HARDWARE_SERVO:
    from gpiozero import AngularServo
    # 서보 떨림이 심하면 pigpio 사용 권장:
    #   sudo pigpiod 실행 후 아래 두 줄 주석 해제
    # from gpiozero.pins.pigpio import PiGPIOFactory
    # from gpiozero import Device; Device.pin_factory = PiGPIOFactory()
    servo = AngularServo(18, min_angle=0, max_angle=90, min_pulse_width=0.0005, max_pulse_width=0.0025)
    servo.angle = SERVO_CLOSED_ANGLE  # 초기 상태: 닫힘


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
    time.sleep(GATE_OPEN_SECONDS)  # 차량 통과 대기
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




def draw_preview(frame, plate, conf):
    view = frame.copy()
    if PLATE_ROI is not None:
        x1, y1, x2, y2 = PLATE_ROI
        cv2.rectangle(view, (x1, y1), (x2, y2), (0, 200, 255), 2)
    label = f"{plate} ({conf:.2f})" if plate else "no plate"
    cv2.putText(view, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.imshow("Entry Gate (q: quit)", view)
    return cv2.waitKey(1) & 0xFF


def main():
    cap = cv2.VideoCapture(ENTRY_CAM_INDEX)
    if not cap.isOpened():
        print(f"입구 카메라({ENTRY_CAM_INDEX}번)를 열 수 없습니다.")
        return

    print("입구 OCR 카메라 및 차단봉 시스템 가동 시작...")
    last_checked_plate = None
    last_checked_time = 0.0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.5)
                continue

            plate, conf = read_4digit_plate(crop_roi(frame))

            if SHOW_PREVIEW and draw_preview(frame, plate, conf) == ord('q'):
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
        if SHOW_PREVIEW:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
