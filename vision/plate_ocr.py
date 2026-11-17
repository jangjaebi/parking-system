"""
숫자 4자리 번호판 OCR (입구 카메라·상단 카메라 공용)

1) 흰 종이 번호판(밝은 직사각형)을 찾아 잘라낸다
2) 잘라낸 이미지를 (전처리 3가지 × Tesseract 모드 3가지)로 읽는다
3) 여러 방법이 같은 숫자를 읽으면 그 결과를 신뢰한다 (투표 방식)

Tesseract는 글자를 맞게 읽어도 신뢰도(conf)를 0~40처럼 낮게 주는 경우가 많아서,
conf만으로 판단하면 맞는 번호도 '불확실'로 버려진다. 그래서 '몇 가지 방법이 같은 답을 냈는지'를
함께 반영해 0~1 사이 신뢰도를 만든다.
"""
import re
from collections import defaultdict

import cv2
import pytesseract

DIGITS_ONLY = "-c tessedit_char_whitelist=0123456789"
# 7: 한 줄 텍스트(번호판에 가장 잘 맞음), 8: 한 단어, 13: 원시 한 줄
PSM_MODES = (7, 8, 13)


def _preprocess_variants(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    # 작은 글자는 키우면 인식률이 크게 오름
    if gray.shape[0] < 150:
        scale = 150.0 / gray.shape[0]
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    # 가장자리에 흰 여백을 두면 Tesseract가 글자 경계를 더 잘 잡음
    gray = cv2.copyMakeBorder(gray, 15, 15, 15, 15, cv2.BORDER_REPLICATE)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    yield gray
    yield otsu
    yield cv2.bitwise_not(otsu)  # 어두운 바탕에 밝은 글자인 경우


def find_plate_candidates(img, max_candidates=3):
    """흰 종이 번호판처럼 보이는 영역을 잘라낸 이미지 목록 (큰 것부터)"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # 글자 때문에 생긴 구멍을 메워 번호판이 하나의 덩어리가 되도록
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    # RETR_LIST: 밝은 바닥·벽 안쪽(차 위)에 있는 번호판도 찾도록 모든 윤곽 검사
    contours, _ = cv2.findContours(bright, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    h_img, w_img = gray.shape[:2]
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < 400 or area > 0.6 * w_img * h_img:
            continue
        if not 1.2 <= w / float(h) <= 6.0:  # 가로로 긴 번호판 모양만
            continue
        boxes.append((area, x, y, w, h))

    crops = []
    for _, x, y, w, h in sorted(boxes, reverse=True)[:max_candidates]:
        m = 2  # 번호판 테두리 바로 안쪽만 (바깥 배경이 섞이지 않게)
        crop = img[y + m:y + h - m, x + m:x + w - m]
        if crop.size:
            crops.append(crop)
    return crops


def _collect_votes(img, votes, best_conf):
    """img를 여러 방법으로 읽어 4자리 결과별 득표수와 최고 conf를 누적"""
    for variant in _preprocess_variants(img):
        for psm in PSM_MODES:
            data = pytesseract.image_to_data(
                variant, config=f"--psm {psm} {DIGITS_ONLY}", output_type=pytesseract.Output.DICT
            )
            seen = set()
            for text, conf in zip(data["text"], data["conf"]):
                cleaned = re.sub(r"\D", "", str(text))
                if len(cleaned) != 4 or cleaned in seen:
                    continue
                seen.add(cleaned)
                try:
                    conf_val = max(float(conf), 0.0) / 100.0  # conf가 '96.5' 같은 문자열이어도 처리
                except (TypeError, ValueError):
                    conf_val = 0.0
                votes[cleaned] += 1
                best_conf[cleaned] = max(best_conf[cleaned], conf_val)


def _score(votes, best_conf):
    if not votes:
        return None, 0.0
    # 득표가 가장 많은 번호 (같으면 conf가 높은 쪽)
    num = max(votes, key=lambda k: (votes[k], best_conf[k]))
    total = sum(votes.values())
    # 득표 기반 신뢰도: 3표 이상 + 다른 후보보다 압도적이면 0.9 이상
    agreement = votes[num] / total
    vote_conf = min(1.0, 0.3 * votes[num]) * agreement
    return num, round(max(best_conf[num], vote_conf), 2)


def read_4digit_plate(img):
    """이미지에서 숫자 4자리를 찾아 (번호 또는 None, 신뢰도 0~1) 반환"""
    if img is None or img.size == 0:
        return None, 0.0

    votes, best_conf = defaultdict(int), defaultdict(float)
    # 1) 번호판처럼 보이는 영역을 잘라서 읽기
    for crop in find_plate_candidates(img):
        _collect_votes(crop, votes, best_conf)
    # 2) 아무것도 못 읽었으면 이미지 전체로 한 번 더
    if not votes:
        _collect_votes(img, votes, best_conf)
    return _score(votes, best_conf)
