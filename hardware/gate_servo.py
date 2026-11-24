"""
입구 차단봉 서보모터 제어 (SG90 기준)

vision/enter_gate.py 에서 가져다 쓰는 모듈입니다.
    from gate_servo import GateServo
    gate = GateServo(use_hardware=True)
    gate.open()
    gate.close()

결선 (BCM 번호)
    서보 신호선(주황) -> GPIO18 (물리 핀 12)
    서보 전원(빨강)   -> 5V
    서보 GND(갈색)    -> GND
    ※ MG996R 같은 큰 서보는 외부 5V 전원을 쓰고 GND를 라즈베리파이와 공통으로 연결

단독 테스트 / 각도 맞추기 (라즈베리파이에서):
    python3 hardware/gate_servo.py
    o = 열기, c = 닫기, 숫자(0~180) = 그 각도로 이동, q = 종료

use_hardware=False 이면 GPIO 없이 화면에 출력만 합니다 (PC 테스트용).
"""
import threading
import time

SERVO_PIN = 18
OPEN_ANGLE = 90              # 차단봉 올림 (모형에 맞게 조정)
CLOSED_ANGLE = 0             # 차단봉 내림

# SG90: 약 0.5ms = 0도, 2.4ms = 180도
# 이 펄스 범위를 0~180도에 맞춰야 '90도' 명령이 실제로 90도 움직입니다.
MIN_PULSE_WIDTH = 0.0005
MAX_PULSE_WIDTH = 0.0024

STEP_DEG = 3                 # 부드럽게 움직일 때 한 번에 움직이는 각도 (0이면 한 번에 이동)
STEP_DELAY = 0.015           # 한 걸음마다 쉬는 시간(초)
SETTLE_SECONDS = 0.3         # 목표 각도 도착 후 신호를 유지하는 시간(초)


class GateServo:
    def __init__(self, use_hardware=True, pin=SERVO_PIN,
                 open_angle=OPEN_ANGLE, closed_angle=CLOSED_ANGLE,
                 min_pulse_width=MIN_PULSE_WIDTH, max_pulse_width=MAX_PULSE_WIDTH,
                 smooth=True):
        self.open_angle = open_angle
        self.closed_angle = closed_angle
        self.smooth = smooth
        self.angle = None
        self._lock = threading.Lock()
        self._servo = None

        if use_hardware:
            from gpiozero import AngularServo
            # 서보 떨림이 심하면 pigpio 사용 (라즈베리파이 4 이하):
            #   sudo apt install pigpio python3-pigpio && sudo pigpiod
            #   그 다음 아래 두 줄 주석 해제
            # from gpiozero import Device
            # from gpiozero.pins.pigpio import PiGPIOFactory; Device.pin_factory = PiGPIOFactory()
            self._servo = AngularServo(
                pin, min_angle=0, max_angle=180,
                min_pulse_width=min_pulse_width, max_pulse_width=max_pulse_width,
            )

        # 시작 상태: 닫힘 (현재 위치를 모르므로 바로 이동)
        self._move(self.closed_angle, smooth=False)

    @property
    def is_open(self):
        return self.angle == self.open_angle

    def open(self):
        self._move(self.open_angle)

    def close(self):
        self._move(self.closed_angle)

    def move_to(self, angle):
        """각도 맞추기용: 0~180도 사이 임의 각도로 이동"""
        self._move(max(0, min(180, angle)))

    def _move(self, target, smooth=None):
        smooth = self.smooth if smooth is None else smooth
        with self._lock:
            if self._servo is None:
                print(f"[서보(가상)] {self.angle} -> {target}도")
                self.angle = target
                return

            start = self.angle
            if smooth and STEP_DEG > 0 and start is not None and start != target:
                step = STEP_DEG if target > start else -STEP_DEG
                for a in range(int(start), int(target), step):
                    self._servo.angle = a
                    time.sleep(STEP_DELAY)
            self._servo.angle = target
            time.sleep(SETTLE_SECONDS)
            # 신호를 끊어 소프트웨어 PWM 때문에 생기는 떨림(지터) 방지
            # SG90은 신호가 없어도 기어 마찰로 위치를 유지함
            self._servo.detach()
            self.angle = target

    def cleanup(self):
        if self._servo is not None:
            self._servo.close()
            self._servo = None


if __name__ == "__main__":
    gate = GateServo(use_hardware=True)
    print("o: 열기 / c: 닫기 / 0~180: 각도 이동 / q: 종료")
    try:
        while True:
            cmd = input("> ").strip().lower()
            if cmd == "q":
                break
            if cmd == "o":
                gate.open()
            elif cmd == "c":
                gate.close()
            elif cmd.isdigit():
                gate.move_to(int(cmd))
            print(f"현재 각도: {gate.angle}")
    except KeyboardInterrupt:
        pass
    finally:
        gate.close()
        gate.cleanup()
