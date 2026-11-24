"""
차단봉 모터 종류 확인 — 라즈베리파이에서 실행

    python3 hardware/servo_check.py

같은 신호를 몇 초 동안 계속 보내서 반응을 봅니다.
  - 일반 서보(0~180도)   : 조금 움직이고 그 자리에서 '멈춰서 버팀'
  - 연속회전 서보(360도) : 신호를 주는 동안 '멈추지 않고 계속 돎'
마지막에 본 것을 입력하면 vision/gate_motor.py의 SERVO_TYPE을 자동으로 바꿔 줍니다.

※ 연속회전 서보면 몇 바퀴 돌 수 있으니, 차단봉이 벽이나 선에 걸리지 않게 확인하세요.
"""
import os
import re
from time import sleep

from gpiozero import Servo

SERVO_PIN = 18
GATE_MOTOR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vision", "gate_motor.py")

# value 0 = 1.5ms(가운데/정지), ±0.3 = 1.2ms / 1.8ms (연속회전이면 천천히 돎)
STEPS = [
    (0.0, "가운데 신호 (1.5ms)"),
    (-0.3, "한쪽 방향 신호 (1.2ms)"),
    (0.3, "반대 방향 신호 (1.8ms)"),
]
HOLD_SECONDS = 3


def set_servo_type(new_type):
    with open(GATE_MOTOR_FILE, encoding="utf-8") as f:
        text = f.read()
    text, count = re.subn(r'^SERVO_TYPE = "[a-z]+"', f'SERVO_TYPE = "{new_type}"', text, flags=re.M)
    if count != 1:
        print("gate_motor.py에서 SERVO_TYPE 줄을 찾지 못했어요. 직접 바꿔 주세요:", new_type)
        return
    with open(GATE_MOTOR_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"vision/gate_motor.py → SERVO_TYPE = \"{new_type}\" 로 바꿨어요.")


servo = Servo(SERVO_PIN, initial_value=None, min_pulse_width=0.0005, max_pulse_width=0.0025)
print(f"GPIO {SERVO_PIN} 모터에 신호를 {HOLD_SECONDS}초씩 3번 보냅니다. 모터를 잘 보세요.")
input("준비되면 Enter > ")

try:
    for value, name in STEPS:
        print(f"\n▶ {name} {HOLD_SECONDS}초 ...")
        servo.value = value
        sleep(HOLD_SECONDS)
        servo.detach()
        sleep(1)
finally:
    servo.detach()
    servo.close()

print("""
어떻게 움직였나요?
  1) 조금 움직이고 바로 멈췄다 (3초 내내 가만히 있었다)   → 일반 서보
  2) 3초 동안 멈추지 않고 계속 돌았다 (2·3번째는 반대로) → 연속회전 서보
     (1번째 '가운데 신호'에서는 연속회전 서보도 거의 안 움직이는 게 정상이에요)""")
answer = input("1 또는 2 입력 (그냥 Enter는 바꾸지 않음) > ").strip()
if answer == "1":
    set_servo_type("positional")
    print("다음: python3 hardware/servo_test.py 로 올림/내림 각도를 맞추세요.")
elif answer == "2":
    set_servo_type("continuous")
    print("다음: 차단봉을 손으로 내린 뒤 python3 hardware/servo_test.py 로 도는 시간을 맞추세요.")
else:
    print("설정은 바꾸지 않았어요.")
