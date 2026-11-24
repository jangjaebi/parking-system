"""
주차칸 상태 LED (3색 LED 5개, A1~A5) — 라즈베리파이에서 실행

    python3 led_status.py          # 서버(/api/parking) 상태를 LED에 표시
    python3 led_status.py test     # 서버 없이 LED만 테스트 (빨강 → 노랑 → 초록)

서버(backend/app.py)를 먼저 켜 둔 뒤 실행하세요. 1초마다 GET /api/parking 을 읽어 LED를 바꿉니다.

LED 색 (발표 자료와 같음)
    빨강 : 칸에 차가 있음 (is_occupied)         — 예약 차량이든 미예약 차량이든
    노랑 : 예약됨, 아직 차 없음 (is_reserved)
    초록 : 이용 가능
    깜빡임 : 앱 경고가 있는 칸 (alarm)
        - 무단 주차(UNAUTHORIZED) → 빨강 깜빡임
        - 자리 확인(WRONG_SPOT)   → 노랑 깜빡임
    서버 연결이 5번 연속 실패하면 → 전부 끔 (오래된 정보를 보여주지 않기 위해)

배선 (BCM 번호 = 확장보드에 적힌 GPIO 번호)
    파란 다리는 쓰지 않습니다. 노랑 = 빨강 + 초록 동시에 켜기
    각 색 다리 → 220Ω 저항 → GPIO, 공통 다리(제일 긴 다리) → GND

    칸   빨강      초록
    A1   GPIO17    GPIO27
    A2   GPIO22    GPIO25
    A3   GPIO5     GPIO6
    A4   GPIO13    GPIO19
    A5   GPIO16    GPIO26

    GPIO18(서보), GPIO23·24(초음파)는 다른 부품이 쓰고 있어서 피했습니다.
"""
import json
import sys
import time
import urllib.request

from gpiozero import PWMLED

# ───────── 내 환경에 맞게 고치는 값 ─────────
SERVER_URL = "http://127.0.0.1:5000"   # 서버를 다른 컴퓨터에서 켰다면 그 IP로
POLL_SECONDS = 1.0                     # 서버 상태 읽는 간격
BLINK_SECONDS = 0.5                    # 경고 깜빡임 속도
MAX_FAILS = 5                          # 이만큼 연속 실패하면 LED 전부 끔

# 공통 다리를 GND에 꽂았으면 True (공통 캐소드), 3.3V에 꽂았으면 False (공통 애노드)
COMMON_CATHODE = True

# 노랑이 연두색처럼 보이면 낮추세요 (0.0 ~ 1.0)
GREEN_IN_YELLOW = 0.4

# 칸 이름: (빨강 GPIO, 초록 GPIO)
LED_PINS = {
    "A1": (17, 27),
    "A2": (22, 25),
    "A3": (5, 6),
    "A4": (13, 19),
    "A5": (16, 26),
}
# ──────────────────────────────────────────

# 색 이름 → (빨강 밝기, 초록 밝기)
COLORS = {
    "red": (1.0, 0.0),
    "yellow": (1.0, GREEN_IN_YELLOW),
    "green": (0.0, 1.0),
    "off": (0.0, 0.0),
}

leds = {
    space_id: (PWMLED(r, active_high=COMMON_CATHODE), PWMLED(g, active_high=COMMON_CATHODE))
    for space_id, (r, g) in LED_PINS.items()
}


def set_color(space_id, color):
    red, green = leds[space_id]
    red.value, green.value = COLORS[color]


def all_off():
    for space_id in leds:
        set_color(space_id, "off")


def color_for(space):
    """서버가 준 칸 정보 하나 → LED 색"""
    if space.get("is_occupied"):
        return "red"
    if space.get("is_reserved"):
        return "yellow"
    return "green"


def fetch_spaces():
    """GET /api/parking → {"A1": {...}, ...}. 실패하면 None"""
    try:
        with urllib.request.urlopen(f"{SERVER_URL}/api/parking", timeout=2) as res:
            body = json.loads(res.read().decode("utf-8"))
        return {s["space_id"]: s for s in body.get("data", [])}
    except Exception as e:
        print(f"[서버 연결 실패] {e}")
        return None


def run_test():
    """서버 없이 배선 확인: 전체 빨강 → 노랑 → 초록, 그다음 칸 순서대로 하나씩"""
    for color in ["red", "yellow", "green"]:
        print("전체", color)
        for space_id in leds:
            set_color(space_id, color)
        time.sleep(2)
    all_off()
    for space_id in leds:
        print(space_id, "빨강 → 초록")
        set_color(space_id, "red")
        time.sleep(0.7)
        set_color(space_id, "green")
        time.sleep(0.7)
        set_color(space_id, "off")
    print("테스트 끝")


def run_server_mode():
    print(f"서버 {SERVER_URL} 상태를 LED에 표시합니다. (Ctrl+C 종료)")
    spaces = {}         # 마지막으로 받은 칸 정보
    fails = 0
    last_poll = 0.0
    last_shown = {}

    while True:
        now = time.monotonic()
        if now - last_poll >= POLL_SECONDS:
            last_poll = now
            data = fetch_spaces()
            if data is None:
                fails += 1
                if fails == MAX_FAILS:
                    print("서버에 계속 연결되지 않아 LED를 끕니다.")
                if fails >= MAX_FAILS:
                    spaces = {}
            else:
                if fails >= MAX_FAILS:
                    print("서버 다시 연결됨")
                fails = 0
                spaces = data

        blink_on = int(now / BLINK_SECONDS) % 2 == 0
        for space_id in leds:
            space = spaces.get(space_id)
            if space is None:
                color = "off"
            else:
                color = color_for(space)
                if (space.get("alarm") or "NONE") != "NONE" and not blink_on:
                    color = "off"          # 경고 칸은 켜졌다 꺼졌다
            set_color(space_id, color)

            # 상태가 바뀔 때만 출력 (깜빡임은 출력 안 함)
            summary = (color_for(space), space.get("alarm")) if space else None
            if summary != last_shown.get(space_id):
                last_shown[space_id] = summary
                if summary:
                    alarm = "" if summary[1] in (None, "NONE") else f" (경고: {summary[1]})"
                    print(f"[LED] {space_id} -> {summary[0]}{alarm}")

        time.sleep(0.05)


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "test":
            run_test()
        else:
            run_server_mode()
    except KeyboardInterrupt:
        pass
    finally:
        all_off()
        print("LED 종료")
