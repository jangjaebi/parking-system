"""
입구 차단봉 모터 단독 테스트 — 라즈베리파이에서 실행

    python3 hardware/servo_test.py

설정은 vision/gate_motor.py 한 곳에서 읽어 옴 (여기서 맞춘 값이 enter_gate.py에도 그대로 적용)

[시작 전] 차단봉을 손으로 '내린 상태'로 맞춰 두세요. (연속회전 서보는 위치를 기억하지 못함)

명령
    o   : 올림          c : 내림          t : 올렸다 3초 뒤 내리기 (실제 입차 동작)
    +   : 올리는 방향으로 조금 (0.05초)   - : 내리는 방향으로 조금  → 위치가 틀어졌을 때 맞추기
    s   : 지금을 '내린 상태'로 기억 (+/-로 맞춘 뒤)
    숫자 : (일반 서보만) 그 각도로 이동 0~180
    q   : 종료 (올라가 있으면 내리고 끝남)

연속회전 서보 값 맞추는 순서
  1) o → 차단봉이 90도보다 덜 올라가면 gate_motor.py의 OPEN_SECONDS를 늘리고, 더 올라가면 줄이기
  2) c → 원래 위치로 정확히 돌아오는지 보고 CLOSE_SECONDS 조절
  3) o를 눌렀는데 아래로 돌면 OPEN_DIRECTION = -1
  4) 값을 바꿨으면 이 프로그램을 다시 실행해서 t로 여러 번 반복해도 위치가 안 밀리는지 확인

배선
    갈색(GND) → GND (물리 6번)   빨강(전원) → 5V (물리 2번)   주황/노랑(신호) → GPIO18 (물리 12번)
"""
import os
import sys
from time import sleep

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vision"))
import gate_motor  # noqa: E402
from gate_motor import GateMotor  # noqa: E402

gate = GateMotor(use_hardware=True)
print("차단봉 모터:", gate.describe())
print("o 올림 / c 내림 / t 올렸다 내리기 / + - 조금씩 / s 지금을 내림 상태로 / q 종료")

try:
    while True:
        cmd = input("> ").strip().lower()
        if cmd == "q":
            break
        elif cmd == "o":
            if gate.is_open:
                print("이미 올라간 상태로 기억하고 있어요. (위치가 틀렸으면 +/-로 내린 뒤 s)")
            gate.open()
        elif cmd == "c":
            gate.close()
        elif cmd == "t":
            gate.open()
            sleep(3)
            gate.close()
        elif cmd == "+":
            gate.nudge(gate_motor.OPEN_DIRECTION)
        elif cmd == "-":
            gate.nudge(-gate_motor.OPEN_DIRECTION)
        elif cmd == "s":
            gate.is_open = False
            print("지금 위치를 '내린 상태'로 기억했어요.")
        elif cmd.isdigit() and 0 <= int(cmd) <= 180:
            if gate_motor.SERVO_TYPE == "continuous":
                print("연속회전 서보는 각도로 움직일 수 없어요. o / c / + / - 를 쓰세요.")
            else:
                gate.goto(int(cmd))
                print(f"→ {cmd}도")
        else:
            print("o, c, t, +, -, s, q (일반 서보는 0~180 숫자도 가능)")
except KeyboardInterrupt:
    pass
finally:
    gate.cleanup()
    print("모터 종료 (내림 상태)")
