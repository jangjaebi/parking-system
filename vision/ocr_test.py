"""
OCR 단독 테스트 도구 (서버 없이 실행 가능)

카메라 화면에 번호판 인식 결과를 띄워서, 실제 서버·차단봉과 연결하기 전에
'카메라 위치·조명·번호판 크기에서 숫자 4자리가 잘 읽히는지' 확인한다.

실행 예:
    python ocr_test.py            # 0번 카메라
    python ocr_test.py 1          # 1번 카메라 (번호 확인: python camera_util.py)
    python ocr_test.py photo.jpg  # 사진 파일 한 장 테스트

키:  q 종료 / s 현재 화면 저장(ocr_samples 폴더) / c 통계 초기화
"""
import os
import sys
import threading
import time
from collections import Counter

import cv2
import numpy as np
import pytesseract

from camera_util import open_camera
from plate_ocr import find_plate_candidates, read_4digit_plate

OCR_MIN_CONFIDENCE = 0.70  # 서버와 같은 기준

# 윈도우에서 Tesseract를 PATH에 안 넣었을 때 자동으로 찾기
WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if os.name == "nt" and os.path.exists(WINDOWS_TESSERACT):
    pytesseract.pytesseract.tesseract_cmd = WINDOWS_TESSERACT


def check_tesseract():
    try:
        print("Tesseract 버전:", pytesseract.get_tesseract_version())
    except Exception:
        print("Tesseract를 찾을 수 없습니다. 설치 후 다시 실행하세요.")
        print(" - 윈도우: UB Mannheim 설치 파일로 설치 (기본 경로 C:\\Program Files\\Tesseract-OCR)")
        print(" - 라즈베리파이: sudo apt install tesseract-ocr")
        sys.exit(1)


def draw_result(frame, plate, conf, elapsed, stats):
    view = frame.copy()
    ok = plate is not None and conf >= OCR_MIN_CONFIDENCE
    color = (0, 200, 0) if ok else (0, 0, 255)
    if plate is None:
        line1 = "NO PLATE"
    else:
        line1 = f"{plate}  conf {conf:.2f}  {'OK' if ok else 'LOW'}"
    cv2.rectangle(view, (0, 0), (view.shape[1], 70), (0, 0, 0), -1)
    cv2.putText(view, line1, (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)

    total = sum(stats.values())
    if total:
        top, count = stats.most_common(1)[0]
        line2 = f"OCR {elapsed:.2f}s | most read: {top} ({count}/{total})"
    else:
        line2 = f"OCR {elapsed:.2f}s"
    cv2.putText(view, line2, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    return view


def show_crop(frame):
    """번호판으로 찾은 영역을 별도 창에 표시 (빈 창이면 번호판 자체를 못 찾는 것)"""
    crops = find_plate_candidates(frame)
    if crops:
        crop = crops[0]
        scale = 120.0 / max(crop.shape[0], 1)
        cv2.imshow("Plate crop", cv2.resize(crop, None, fx=scale, fy=scale))
    else:
        show_crop_blank()


def show_crop_blank():
    blank = np.full((60, 240, 3), 255, np.uint8)
    cv2.putText(blank, "not found", (10, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    cv2.imshow("Plate crop", blank)


def test_image(path):
    frame = cv2.imread(path)
    if frame is None:
        print("사진을 열 수 없습니다:", path)
        return
    start = time.time()
    plate, conf = read_4digit_plate(frame)
    print(f"결과: {plate}  신뢰도: {conf:.2f}  ({time.time() - start:.2f}초)")
    cv2.imshow("OCR test", draw_result(frame, plate, conf, time.time() - start, Counter()))
    show_crop(frame)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


class OcrWorker(threading.Thread):
    """화면 표시와 별도로 뒤에서 OCR만 반복 (화면이 OCR을 기다리지 않아 끊기지 않음)"""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.frame = None
        self.result = (None, 0.0, 0.0)  # (번호, 신뢰도, 걸린 시간)
        self.crop = None
        self.stats = Counter()
        self.running = True

    def submit(self, frame):
        with self.lock:
            self.frame = frame

    def run(self):
        while self.running:
            with self.lock:
                frame, self.frame = self.frame, None
            if frame is None:
                time.sleep(0.01)
                continue
            start = time.time()
            plate, conf = read_4digit_plate(frame)
            crops = find_plate_candidates(frame)
            elapsed = time.time() - start
            with self.lock:
                self.result = (plate, conf, elapsed)
                self.crop = crops[0] if crops else None
                if plate:
                    self.stats[plate] += 1
            if plate:
                print(f"{plate}  conf {conf:.2f}  {'OK' if conf >= OCR_MIN_CONFIDENCE else 'LOW'}  ({elapsed:.2f}s)")

    def snapshot(self):
        with self.lock:
            return self.result, self.crop, Counter(self.stats)

    def clear_stats(self):
        with self.lock:
            self.stats.clear()


def test_camera(index):
    cap = open_camera(index)
    if not cap.isOpened():
        print(f"{index}번 카메라를 열 수 없습니다. 카메라 번호 확인: python camera_util.py")
        return

    worker = OcrWorker()
    worker.start()
    os.makedirs("ocr_samples", exist_ok=True)
    print("카메라 테스트 시작 (q 종료, s 화면 저장, c 통계 초기화)")

    fps, frames, fps_time = 0.0, 0, time.time()
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.05)
                continue

            worker.submit(frame.copy())  # OCR은 가장 최근 화면만 처리
            (plate, conf, elapsed), crop, stats = worker.snapshot()

            frames += 1
            if time.time() - fps_time >= 1.0:
                fps, frames, fps_time = frames / (time.time() - fps_time), 0, time.time()

            view = draw_result(frame, plate, conf, elapsed, stats)
            cv2.putText(view, f"{fps:.0f} fps", (view.shape[1] - 90, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
            cv2.imshow("OCR test", view)
            if crop is not None and crop.size:
                scale = 120.0 / max(crop.shape[0], 1)
                cv2.imshow("Plate crop", cv2.resize(crop, None, fx=scale, fy=scale))
            else:
                show_crop_blank()

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            if key == ord('s'):
                name = os.path.join("ocr_samples", f"sample_{int(time.time())}.jpg")
                cv2.imwrite(name, frame)
                print("저장:", name)
            if key == ord('c'):
                worker.clear_stats()
    finally:
        worker.running = False
        cap.release()
        cv2.destroyAllWindows()
        _, _, stats = worker.snapshot()
        if stats:
            print("\n인식 통계:", dict(stats.most_common()))


if __name__ == "__main__":
    check_tesseract()
    arg = sys.argv[1] if len(sys.argv) > 1 else "0"
    if arg.isdigit():
        test_camera(int(arg))
    else:
        test_image(arg)
