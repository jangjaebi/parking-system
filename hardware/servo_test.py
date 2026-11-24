"""
입구 차단봉 서보모터(SG90) 단독 테스트 — 라즈베리파이에서 실행

    python3 servo_test.py

명령:
    o   : 올림 (OPEN_ANGLE)
    c   : 내림 (CLOSED_ANGLE)
    t   : 올렸다 3초 뒤 내리기 (실제 입차 동작과 같음)
    숫자 : 그 각도로 이동 (0~180) → 모형에 맞는 올림/내림 각도 찾기
    q   : 종료 (내림 상태로 끝남)

맞는 각도를 찾으면 vision/enter_gate.py의 SERVO_OPEN_ANGLE / SERVO_CLOSED_ANGLE에 같은 값을 적으세요.

배선 (SG90)
    갈색(GND)        → GND (물리 6번 핀 등)
    빨강(전원)        → 5V  (물리 2번 또는 4번 핀)
    주황/노랑(신호)   → GPIO18 (물리 12번 핀)
"""
from time import sleep

from gpiozero import AngularServo

SERVO_PIN = 18
CLOSED_ANGLE = 0
OPEN_ANGLE = 90
MOVE_SECONDS = 0.6   # 움직인 뒤 신호를 끊어 떨림 방지

servo = AngularServo(SERVO_PIN, min_angle=0, max_angle=180,
                     min_pulse_width=0.0005, max_pulse_width=0.0025,
                     initial_angle=CLOSED_ANGLE)


def move(angle):
    servo.angle = angle
    sleep(MOVE_SECONDS)
    servo.detach()
    print(f"→ {angle}도")


try:
    move(CLOSED_ANGLE)
    print("o: 올림 / c: 내림 / t: 올렸다 내리기 / 0~180: 각도 / q: 종료")
    while True:
        cmd = input("> ").strip().lower()
        if cmd == "q":
            break
        elif cmd == "o":
            move(OPEN_ANGLE)
        elif cmd == "c":
            move(CLOSED_ANGLE)
        elif cmd == "t":
            move(OPEN_ANGLE)
            sleep(3)
            move(CLOSED_ANGLE)
        elif cmd.isdigit() and 0 <= int(cmd) <= 180:
            move(int(cmd))
        else:
            print("o, c, t, 0~180 숫자, q 중 하나를 입력하세요.")
except KeyboardInterrupt:
    pass
finally:
    move(CLOSED_ANGLE)
    servo.close()
    print("서보 종료 (내림 상태)")
