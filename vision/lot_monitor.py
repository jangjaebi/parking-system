"""
상단 카메라 — 주차면 A1~A5 감시
1) 빈 주차장 기준 화면과 비교해 칸별 점유 여부를 판단 → /api/occupancy
   (서버가 예약 여부와 비교해 무단 주차를 판정하고, 출차 시 예약을 자동 종료)
2) 차가 들어온 칸은 지붕의 숫자 4자리 번호판을 읽어 → /api/ocr
   (예약한 차가 자기 칸에 섰는지 서버가 대조)
"""
import time

import cv2
import numpy as np
import requests

from plate_ocr import read_4digit_plate

SERVER_URL = "http://127.0.0.1:5000"
LOT_CAM_INDEX = 1            # 내부 주차장 감시 카메라 번호 (입구 카메라가 0번일 때 1 또는 2)
SHOW_PREVIEW = True          # 칸 좌표 맞출 때 True (r: 기준 화면 다시 찍기, q: 종료)

# 카메라 화면(640x480) 내 A1~A5 주차칸 좌표 (x1, y1, x2, y2) — 모형에 맞게 SHOW_PREVIEW로 확인하며 조정
PARKING_ROIS = {
    "A1": (108, 141, 202, 448),
    "A2": (282, 121, 420, 445),
    "A3": (506, 104, 686, 453),
    "A4": (758, 105, 932, 450),
    "A5": (1011, 116, 1147, 442),
}

PIXEL_DIFF_THRESHOLD = 30    # 기준 화면과 픽셀 밝기 차이 기준
OCCUPANCY_THRESHOLD = 0.15   # 칸 면적 중 변화한 비율이 이 이상이면 '차 있음'
STABLE_FRAMES = 5            # 같은 결과가 연속 N프레임 나와야 상태 변경 (손·그림자 오감지 방지)
OCR_DELAY = 1.5              # 차가 들어오고 OCR 하기까지 대기(초) — 다른 칸 감시는 멈추지 않음
OCR_MAX_TRIES = 3            # 번호판을 못 읽으면 재시도 횟수
OCR_RETRY_INTERVAL = 1.0
LOOP_INTERVAL = 0.2


def post(path, payload):
    """서버 전송. 성공하면 응답 JSON, 실패하면 None"""
    try:
        res = requests.post(f"{SERVER_URL}{path}", json=payload, timeout=3)
        return res.json() if res.ok else None
    except (requests.RequestException, ValueError) as e:
        print(f"[서버 통신 오류] {path}: {e}")
        return None


def send_occupancy(space_id: str, occupied: bool) -> bool:
    res = post("/api/occupancy", {"space_id": space_id, "occupied": occupied})
    if res:
        print(f"[점유] {space_id} -> {'차 있음' if occupied else '빈칸'} | {res.get('message')}")
    return res is not None


def send_lot_ocr(space_id: str, car_number, confidence: float):
    res = post("/api/ocr", {
        "space_id": space_id,
        "detected_car_number": car_number,
        "confidence": round(confidence, 2)
    })
    if res:
        print(f"[번호판 대조] {space_id} '{car_number}' -> {res.get('result_code')} ({res.get('message')})")




def to_gray(frame):
    return cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (21, 21), 0)


def capture_baseline(cap):
    """카메라 노출이 안정될 때까지 프레임을 버린 뒤 빈 주차장 기준 화면 캡처"""
    for _ in range(15):
        cap.read()
        time.sleep(0.05)
    ret, frame = cap.read()
    return to_gray(frame) if ret else None


def is_space_occupied(base_gray, gray, roi):
    x1, y1, x2, y2 = roi
    diff = cv2.absdiff(base_gray[y1:y2, x1:x2], gray[y1:y2, x1:x2])
    _, thresh = cv2.threshold(diff, PIXEL_DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
    # numpy bool은 JSON으로 보낼 수 없어서 파이썬 bool로 변환
    return bool(np.count_nonzero(thresh) / thresh.size > OCCUPANCY_THRESHOLD)


def draw_preview(frame, reported):
    view = frame.copy()
    for space_id, (x1, y1, x2, y2) in PARKING_ROIS.items():
        color = (0, 0, 255) if reported[space_id] else (0, 200, 0)
        cv2.rectangle(view, (x1, y1), (x2, y2), color, 2)
        cv2.putText(view, space_id, (x1 + 5, y1 + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    cv2.imshow("Lot Monitor (r: reset baseline, q: quit)", view)
    return cv2.waitKey(1) & 0xFF


def main():
    cap = cv2.VideoCapture(LOT_CAM_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    if not cap.isOpened():
        print(f"내부 감시 카메라({LOT_CAM_INDEX}번)를 열 수 없습니다.")
        return

    print("빈 주차장 상태에서 기준 화면을 캡처합니다... (차를 모두 빼 두세요)")
    base_gray = capture_baseline(cap)
    if base_gray is None:
        print("기준 화면 캡처 실패")
        return

    # reported: 서버에 반영된 상태 / streak: 현재 결과가 몇 프레임째 연속인지
    reported = {sid: False for sid in PARKING_ROIS}
    streak = {sid: 0 for sid in PARKING_ROIS}
    # 시작 시 모든 칸을 '빈칸'으로 서버와 맞춤 (서버가 꺼져 있으면 아래 루프에서 다시 시도)
    synced = {sid: send_occupancy(sid, False) for sid in PARKING_ROIS}
    # OCR 예약: space_id -> (실행 시각, 남은 시도 횟수)
    pending_ocr = {}

    print("주차면 점유 감지 + 번호판 대조 시작...")
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.5)
                continue

            gray = to_gray(frame)
            now = time.time()

            for space_id, roi in PARKING_ROIS.items():
                occupied = is_space_occupied(base_gray, gray, roi)

                # 디바운스: 서버 상태와 다른 결과가 연속 STABLE_FRAMES번 나와야 변경
                if occupied != reported[space_id]:
                    streak[space_id] += 1
                else:
                    streak[space_id] = 0

                if streak[space_id] >= STABLE_FRAMES or not synced[space_id]:
                    target = occupied if streak[space_id] >= STABLE_FRAMES else reported[space_id]
                    # 전송에 성공했을 때만 상태 갱신 → 서버가 꺼져 있어도 다음 루프에서 재전송
                    if send_occupancy(space_id, target):
                        synced[space_id] = True
                        if target != reported[space_id]:
                            reported[space_id] = target
                            streak[space_id] = 0
                            if target:
                                pending_ocr[space_id] = (now + OCR_DELAY, OCR_MAX_TRIES)
                            else:
                                pending_ocr.pop(space_id, None)

            # 정차 대기 시간이 지난 칸만 번호판 OCR (다른 칸 감시를 멈추지 않음)
            for space_id, (run_at, tries_left) in list(pending_ocr.items()):
                if now < run_at:
                    continue
                if not reported[space_id]:
                    pending_ocr.pop(space_id)
                    continue
                x1, y1, x2, y2 = PARKING_ROIS[space_id]
                car_num, conf = read_4digit_plate(frame[y1:y2, x1:x2])
                if car_num is None and tries_left > 1:
                    pending_ocr[space_id] = (now + OCR_RETRY_INTERVAL, tries_left - 1)
                    continue
                pending_ocr.pop(space_id)
                send_lot_ocr(space_id, car_num, conf)

            if SHOW_PREVIEW:
                key = draw_preview(frame, reported)
                if key == ord('q'):
                    break
                if key == ord('r'):
                    print("기준 화면을 다시 캡처합니다... (차를 모두 빼 두세요)")
                    new_base = capture_baseline(cap)
                    if new_base is not None:
                        base_gray = new_base

            time.sleep(LOOP_INTERVAL)
    finally:
        cap.release()
        if SHOW_PREVIEW:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
