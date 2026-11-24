"""
상단 카메라 주차칸(A1~A5) 위치 지정 도구

    python setup_rois.py 1      # 1번 카메라로 (번호 확인: python camera_util.py)

1) 카메라 화면이 뜨면 모형 위치를 맞추고 SPACE 키 → 그 화면으로 고정
2) 마우스로 칸을 하나씩 드래그 → ENTER (5칸 반복, 순서는 상관없음: 왼쪽부터 A1~A5로 자동 정렬)
3) 5칸을 다 그렸으면 ESC → parking_rois.json 저장
   lot_monitor.py가 다음 실행부터 이 좌표를 사용

※ 칸은 '차 지붕 번호판이 다 들어올 만큼' 넉넉하게, 옆 칸과는 겹치지 않게 그리세요.
"""
import json
import os

import cv2

from camera_util import camera_index_from_args, open_camera
from lot_monitor import LOT_CAM_HEIGHT, LOT_CAM_WIDTH

SPACES = ["A1", "A2", "A3", "A4", "A5"]
ROI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parking_rois.json")


def capture_still(cam_index):
    cap = open_camera(cam_index, LOT_CAM_WIDTH, LOT_CAM_HEIGHT)
    if not cap.isOpened():
        print(f"{cam_index}번 카메라를 열 수 없습니다. 카메라 번호 확인: python camera_util.py")
        return None
    print("카메라 위치를 맞춘 뒤 SPACE를 누르세요. (q: 취소)")
    frame = None
    try:
        while True:
            ret, f = cap.read()
            if not ret:
                continue
            frame = f
            view = f.copy()
            cv2.putText(view, "SPACE: capture / q: quit", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.imshow("Setup", view)
            key = cv2.waitKey(1) & 0xFF
            if key == ord(' '):
                return frame
            if key == ord('q'):
                return None
    finally:
        cap.release()
        cv2.destroyAllWindows()


def main():
    cam_index = camera_index_from_args(1)
    frame = capture_still(cam_index)
    if frame is None:
        return

    print("칸을 하나 드래그 → ENTER, 5칸 반복 후 ESC")
    boxes = cv2.selectROIs("Draw A1~A5 (drag -> ENTER, finish -> ESC)", frame, showCrosshair=False)
    cv2.destroyAllWindows()
    boxes = [tuple(int(v) for v in b) for b in boxes if b[2] > 0 and b[3] > 0]

    if len(boxes) != len(SPACES):
        print(f"{len(boxes)}칸을 그렸어요. 정확히 {len(SPACES)}칸을 그려야 합니다. 다시 실행해 주세요.")
        return

    # 왼쪽부터 A1, A2, ... 순서로 이름 붙이기
    boxes.sort(key=lambda b: b[0])
    rois = {sid: [x, y, x + w, y + h] for sid, (x, y, w, h) in zip(SPACES, boxes)}

    with open(ROI_FILE, "w", encoding="utf-8") as f:
        h, w = frame.shape[:2]
        json.dump({"camera_index": cam_index, "resolution": [w, h], "rois": rois},
                  f, ensure_ascii=False, indent=2)

    # 결과 확인 화면
    view = frame.copy()
    for sid, (x1, y1, x2, y2) in rois.items():
        cv2.rectangle(view, (x1, y1), (x2, y2), (0, 200, 0), 2)
        cv2.putText(view, sid, (x1 + 5, y1 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 0), 2)
    print(f"저장 완료: {ROI_FILE} ({w}x{h} 기준)")
    for sid, r in rois.items():
        print(f"  {sid}: {r}")
    print("확인 화면에서 아무 키나 누르면 닫힙니다.")
    cv2.imshow("Saved ROIs", view)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
