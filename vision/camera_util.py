"""
카메라 열기 (입구·상단·OCR 테스트 공용) + 카메라 찾기 도구

카메라 번호는 컴퓨터마다, USB를 꽂는 포트마다 달라질 수 있다.
어느 번호가 어느 카메라인지 모르겠으면 이 파일을 직접 실행:

    python camera_util.py

연결된 카메라를 한 화면에 번호와 함께 보여 준다.
확인한 번호로 실행:  python ocr_test.py 1 / python enter_gate.py 1 / python lot_monitor.py 2
"""
import os
import sys
import threading
import time

import cv2
import numpy as np


def open_camera(index, width=640, height=480):
    """세 프로그램 모두 같은 방식으로 열어야 같은 번호가 같은 카메라를 가리킴"""
    cap = cv2.VideoCapture(index)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


def camera_index_from_args(default):
    """실행할 때 붙인 숫자가 있으면 그 번호의 카메라 사용 (예: python enter_gate.py 1)"""
    for arg in sys.argv[1:]:
        if arg.isdigit():
            return int(arg)
    return default


def find_cameras(max_index=4):
    """0~max_index번 카메라를 하나씩 열어 첫 화면을 찍어 옴 → {번호: 화면}"""
    found = {}
    for i in range(max_index + 1):
        cap = open_camera(i)
        if not cap.isOpened():
            cap.release()
            continue
        frame = None
        for _ in range(10):  # 카메라가 켜지는 동안 몇 장 버림
            ret, f = cap.read()
            if ret:
                frame = f
        cap.release()
        if frame is not None:
            found[i] = frame
    return found


def _show_cameras():
    print("연결된 카메라를 찾는 중... (몇 초 걸려요)")
    found = find_cameras()
    if not found:
        print("열 수 있는 카메라가 없습니다. USB 카메라가 꽂혀 있는지 확인하세요.")
        return
    tiles = []
    for i, frame in found.items():
        tile = cv2.resize(frame, (400, 300))
        cv2.rectangle(tile, (0, 0), (400, 50), (0, 0, 0), -1)
        cv2.putText(tile, f"camera {i}", (10, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 2)
        tiles.append(tile)
    print("찾은 카메라 번호:", list(found))
    print("창을 보고 USB 카메라의 번호를 확인하세요. 아무 키나 누르면 닫힙니다.")
    cv2.imshow("Cameras", np.hstack(tiles))
    cv2.waitKey(0)
    cv2.destroyAllWindows()


class BackgroundOcr(threading.Thread):
    """
    화면 표시와 별도로 뒤에서 OCR만 돌리는 작업자
    - submit(frame): 가장 최근 화면만 맡김 (밀린 화면은 버림)
    - take_result(): 새 결과가 나왔으면 (번호, 신뢰도), 아직이면 None
    - latest(): 화면 표시용 마지막 결과
    """

    def __init__(self, ocr_func):
        super().__init__(daemon=True)
        self.ocr_func = ocr_func
        self.lock = threading.Lock()
        self.frame = None
        self.new_result = None
        self.last_result = (None, 0.0)
        self.running = True

    def submit(self, frame):
        with self.lock:
            self.frame = frame

    def clear(self):
        with self.lock:
            self.frame = None
            self.new_result = None
            self.last_result = (None, 0.0)

    def take_result(self):
        with self.lock:
            result, self.new_result = self.new_result, None
            return result

    def latest(self):
        with self.lock:
            return self.last_result

    def run(self):
        while self.running:
            with self.lock:
                frame, self.frame = self.frame, None
            if frame is None:
                time.sleep(0.01)
                continue
            result = self.ocr_func(frame)
            with self.lock:
                self.new_result = result
                self.last_result = result


if __name__ == "__main__":
    _show_cameras()
