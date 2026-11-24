"""
입구 차단봉 모터 제어 (enter_gate.py와 hardware/servo_test.py가 같이 사용)

모터 종류에 따라 SERVO_TYPE을 고르세요.
  - "continuous": 360도 연속회전 서보 (SG90 360, FS90R 등)
        각도를 정할 수 없고 '정해진 속도로 몇 초 돌리기'만 가능
        → 한쪽으로 OPEN_SECONDS 동안 돌려서 올리고, 반대로 CLOSE_SECONDS 동안 돌려서 내림
        → 각도 명령을 주면 같은 방향으로 계속 돌아 한 바퀴(270도 더) 도는 증상이 생김
  - "positional": 일반 서보 (0~180도 위치 지정, 일반 SG90)

연속회전 서보는 위치를 기억하지 못하므로, 프로그램을 켜기 전에
차단봉을 '내린 상태'로 손으로 맞춰 두세요.
값 맞추기: python3 hardware/servo_test.py
"""
import threading
import time

# ── 배선 ──
SERVO_PIN = 18                # 신호선(주황/노랑) → GPIO18 (물리 12번 핀)

# ── 모터 종류 ──
SERVO_TYPE = "continuous"     # "continuous"(360도 연속회전) / "positional"(일반 0~180도)

# ── 연속회전 서보 설정 (servo_test.py로 차단봉이 딱 90도 움직이도록 맞추기) ──
SPEED = 0.5                   # 회전 속도 0.1 ~ 1.0 (너무 빠르면 멈추는 위치가 들쭉날쭉)
OPEN_SECONDS = 0.08           # 올릴 때 도는 시간 (90도가 되도록)
CLOSE_SECONDS = 0.09          # 내릴 때 도는 시간 (차단봉 무게 때문에 올릴 때와 다를 수 있음)
OPEN_DIRECTION = -1            # 올릴 때 반대로 돌면 -1로

# ── 일반 서보 설정 ──
CLOSED_ANGLE = 0              # 내림
OPEN_ANGLE = 90               # 올림
MOVE_SECONDS = 0.6            # 움직인 뒤 신호를 끊어 떨림 방지


class GateMotor:
    """use_hardware=False(노트북)면 실제 모터 없이 터미널 출력만"""

    def __init__(self, use_hardware=True):
        self.lock = threading.Lock()
        self.is_open = False      # 시작할 때는 내린 상태라고 가정
        self.device = None
        if not use_hardware:
            return
        if SERVO_TYPE == "continuous":
            from gpiozero import Servo
            # value 0 = 정지(1.5ms), +1/-1 = 양쪽 방향 최고 속도. 처음에는 신호 없음(정지)
            self.device = Servo(SERVO_PIN, initial_value=None,
                                min_pulse_width=0.0005, max_pulse_width=0.0025)
        else:
            from gpiozero import AngularServo
            # SG90: 0.5ms = 0도, 2.5ms = 180도
            self.device = AngularServo(SERVO_PIN, min_angle=0, max_angle=180,
                                       min_pulse_width=0.0005, max_pulse_width=0.0025,
                                       initial_angle=CLOSED_ANGLE)
            time.sleep(MOVE_SECONDS)
            self.device.detach()

    def describe(self):
        if self.device is None:
            return "미사용 (터미널 출력만)"
        if SERVO_TYPE == "continuous":
            return f"GPIO {SERVO_PIN}, 연속회전 (올림 {OPEN_SECONDS}초 / 내림 {CLOSE_SECONDS}초, 속도 {SPEED})"
        return f"GPIO {SERVO_PIN}, 일반 서보 (내림 {CLOSED_ANGLE}도 / 올림 {OPEN_ANGLE}도)"

    # ── 연속회전: 정해진 시간만큼 돌리고 멈춤 ──
    def _spin(self, direction, seconds, speed=None):
        if self.device is None:
            return
        self.device.value = max(-1.0, min(1.0, direction * (speed or SPEED)))
        time.sleep(seconds)
        self.device.value = 0
        self.device.detach()   # 신호를 끊어야 확실히 멈춤 (정지 신호가 살짝 어긋난 모터도)

    # ── 일반 서보: 각도로 이동 ──
    def _goto(self, angle):
        if self.device is None:
            return
        self.device.angle = angle
        time.sleep(MOVE_SECONDS)
        self.device.detach()

    def open(self):
        with self.lock:
            if self.is_open:
                return  # 이미 올라가 있으면 또 돌리지 않음 (연속회전은 한 바퀴 돌아버림)
            print(">>> [모터] 입구 차단봉 올림")
            if SERVO_TYPE == "continuous":
                self._spin(OPEN_DIRECTION, OPEN_SECONDS)
            else:
                self._goto(OPEN_ANGLE)
            self.is_open = True

    def close(self):
        with self.lock:
            if not self.is_open:
                return
            print(">>> [모터] 입구 차단봉 내림")
            if SERVO_TYPE == "continuous":
                self._spin(-OPEN_DIRECTION, CLOSE_SECONDS)
            else:
                self._goto(CLOSED_ANGLE)
            self.is_open = False

    def nudge(self, direction, seconds=0.05):
        """연속회전 서보 위치를 조금씩 맞출 때 (servo_test.py에서 사용)"""
        with self.lock:
            if SERVO_TYPE == "continuous":
                self._spin(direction, seconds)

    def goto(self, angle):
        """일반 서보 각도 테스트 (servo_test.py에서 사용)"""
        with self.lock:
            if SERVO_TYPE != "continuous":
                self._goto(angle)

    def cleanup(self):
        """프로그램 끝낼 때: 올라가 있으면 내리고 정리"""
        self.close()
        if self.device is not None:
            self.device.close()
