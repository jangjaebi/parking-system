"""
상단 카메라 — 주차면 A1~A5 감시
빈 주차장 기준 화면과 비교해 칸별로 차가 있는지만 판단 → /api/occupancy
(번호판은 읽지 않음. 예약·'주차하기' 상태와 비교한 경고 판단은 서버가 하고, 앱에 표시됨)
"""
import json
import os
import time

import cv2
import numpy as np
import requests

from camera_util import camera_index_from_args, open_camera

SERVER_URL = "http://127.0.0.1:5000"
LOT_CAM_INDEX = 1            # 내부 주차장 감시 카메라 번호 (입구 카메라가 0번일 때 1 또는 2)
SHOW_PREVIEW = True          # 칸 좌표 맞출 때 True (r: 기준 화면 다시 찍기, q: 종료)

# 상단 카메라 해상도 (칸 좌표는 이 해상도 기준)
LOT_CAM_WIDTH, LOT_CAM_HEIGHT = 1280, 720

# A1~A5 주차칸 좌표 (x1, y1, x2, y2) — 1280x720 기준, 모형에 맞춰 측정한 값
# python setup_rois.py 로 마우스로 다시 지정하면 parking_rois.json에 저장되고, 아래 값 대신 그 값을 씀
PARKING_ROIS = {
    "A1": (108, 141, 202, 448),
    "A2": (282, 121, 420, 445),
    "A3": (506, 104, 686, 453),
    "A4": (758, 105, 932, 450),
    "A5": (1011, 116, 1147, 442),
}
ROI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parking_rois.json")
ROI_SOURCE = "lot_monitor.py 기본값"
if os.path.exists(ROI_FILE):
    with open(ROI_FILE, encoding="utf-8") as _f:
        _saved = json.load(_f)
    PARKING_ROIS = {sid: tuple(box) for sid, box in _saved["rois"].items()}
    LOT_CAM_INDEX = _saved.get("camera_index", LOT_CAM_INDEX)
    LOT_CAM_WIDTH, LOT_CAM_HEIGHT = _saved.get("resolution", [LOT_CAM_WIDTH, LOT_CAM_HEIGHT])
    ROI_SOURCE = "parking_rois.json"

PIXEL_DIFF_THRESHOLD = 30    # 기준 화면과 픽셀 밝기 차이 기준
OCCUPANCY_THRESHOLD = 0.15   # 칸 면적 중 변화한 비율이 이 이상이면 '차 있음'
STABLE_FRAMES = 5            # 같은 결과가 연속 N프레임 나와야 상태 변경 (손·그림자 오감지 방지)
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
    """빈칸: 초록 / 차 있음: 빨강"""
    view = frame.copy()
    for space_id, (x1, y1, x2, y2) in PARKING_ROIS.items():
        color = (0, 0, 255) if reported[space_id] else (0, 200, 0)
        cv2.rectangle(view, (x1, y1), (x2, y2), color, 2)
        cv2.putText(view, space_id, (x1 + 5, y1 + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    cv2.imshow("Lot Monitor (r: reset baseline, q: quit)", view)
    return cv2.waitKey(1) & 0xFF


def main():
    cam_index = camera_index_from_args(LOT_CAM_INDEX)  # python lot_monitor.py 1 처럼 번호를 붙이면 그 카메라 사용
    cap = open_camera(cam_index, LOT_CAM_WIDTH, LOT_CAM_HEIGHT)

    if not cap.isOpened():
        print(f"내부 감시 카메라({cam_index}번)를 열 수 없습니다. 카메라 번호 확인: python camera_util.py")
        return

    print(f"주차칸 좌표: {ROI_SOURCE} ({LOT_CAM_WIDTH}x{LOT_CAM_HEIGHT} 기준)")
    print("빈 주차장 상태에서 기준 화면을 캡처합니다... (차를 모두 빼 두세요)")
    base_gray = capture_baseline(cap)
    if base_gray is None:
        print("기준 화면 캡처 실패")
        return

    # 카메라가 다른 해상도로 열리면 칸 좌표가 어긋남 (라즈베리파이·다른 카메라에서 흔함)
    h, w = base_gray.shape[:2]
    if (w, h) != (LOT_CAM_WIDTH, LOT_CAM_HEIGHT):
        print(f"[주의] 카메라가 {w}x{h}로 열렸어요. 칸 좌표는 {LOT_CAM_WIDTH}x{LOT_CAM_HEIGHT} 기준이라 어긋날 수 있어요.")
        print("       python setup_rois.py 로 칸을 다시 지정하세요.")

    # reported: 서버에 반영된 상태 / streak: 현재 결과가 몇 프레임째 연속인지
    reported = {sid: False for sid in PARKING_ROIS}
    streak = {sid: 0 for sid in PARKING_ROIS}
    # 시작 시 모든 칸을 '빈칸'으로 서버와 맞춤 (서버가 꺼져 있으면 아래 루프에서 다시 시도)
    synced = {sid: send_occupancy(sid, False) for sid in PARKING_ROIS}
    print("주차면 점유 감지 시작...")
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.5)
                continue

            gray = to_gray(frame)
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
