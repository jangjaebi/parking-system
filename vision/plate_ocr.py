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

import os
import shutil

import cv2
import numpy as np
import pytesseract

# 윈도우: Tesseract를 PATH에 등록하지 않았어도 기본 설치 경로에서 자동으로 찾기
_WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if os.name == "nt" and not shutil.which("tesseract") and os.path.exists(_WINDOWS_TESSERACT):
    pytesseract.pytesseract.tesseract_cmd = _WINDOWS_TESSERACT

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


def _count_char_blobs(binary_crop):
    """잘라낸 영역 안의 '글자 크기' 덩어리 개수 (숫자 4자리면 4에 가까움)"""
    h = binary_crop.shape[0]
    n, _, stats, _ = cv2.connectedComponentsWithStats(binary_crop)
    return sum(1 for i in range(1, n) if stats[i, cv2.CC_STAT_HEIGHT] >= 0.45 * h)


def _text_boxes(gray):
    """
    밝은 바탕 위의 어두운 글자 덩어리 찾기 (배경이 흰 벽·흰 바닥이어도 동작)
    blackhat: 주변보다 어두운 작은 무늬(글자)만 강조
    """
    h_img, w_img = gray.shape[:2]
    k = max(15, h_img // 20)
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    _, chars = cv2.threshold(blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # 가로로 이어 붙여 숫자 4개를 하나의 덩어리로
    joined = cv2.morphologyEx(chars, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, w_img // 40), 3)))
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if h < 0.03 * h_img or h > 0.5 * h_img or w * h < 300:
            continue
        if not 1.5 <= w / float(h) <= 8.0:
            continue
        blobs = _count_char_blobs(chars[y:y + h, x:x + w])
        # 글자가 4개에 가까울수록, 클수록 우선
        boxes.append((abs(blobs - 4), -w * h, x, y, w, h))
    boxes.sort()
    return [(x, y, w, h) for _, _, x, y, w, h in boxes]


def _white_boxes(gray):
    """흰 종이 번호판(밝은 직사각형) 찾기 (배경이 어두울 때 잘 동작)"""
    h_img, w_img = gray.shape[:2]
    _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    # RETR_LIST: 밝은 바닥·벽 안쪽(차 위)에 있는 번호판도 찾도록 모든 윤곽 검사
    contours, _ = cv2.findContours(bright, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < 400 or w * h > 0.6 * w_img * h_img:
            continue
        if not 1.2 <= w / float(h) <= 6.0:
            continue
        boxes.append((-w * h, x + 2, y + 2, w - 4, h - 4))
    boxes.sort()
    return [(x, y, w, h) for _, x, y, w, h in boxes]


def _char_binaries(gray):
    """
    어두운 글자만 흰색으로 남긴 이진 영상 여러 장
    (조명·흐림 정도에 따라 숫자가 서로 붙거나 끊기는 방식이 달라서 몇 가지를 같이 씀)
    """
    out = []
    for block, c in ((31, 10), (41, 12)):
        out.append(cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                         cv2.THRESH_BINARY_INV, block, c))
    k = max(15, gray.shape[0] // 20)
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    _, bh = cv2.threshold(blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(bh)
    return out


def _row_box(row):
    xs = [x for x, _, _, _ in row] + [x + w for x, _, w, _ in row]
    ys = [y for _, y, _, _ in row] + [y + h for _, y, _, h in row]
    return min(xs), min(ys), max(xs), max(ys)


def _overlaps(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    iw = max(0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    smaller = min((ax2 - ax1) * (ay2 - ay1), (bx2 - bx1) * (by2 - by1))
    return smaller > 0 and inter / float(smaller) > 0.5


def _digit_rows(chars, max_rows=3):
    """
    숫자 하나하나를 덩어리로 찾은 뒤, 크기가 비슷하고 옆으로 나란히 놓인 3~6개를 한 줄(번호판)로 묶음
    → 차체·바퀴처럼 숫자 옆에 붙은 무늬는 빠지고, 기울어진 번호판도 찾을 수 있음
    """
    h_img = chars.shape[0]
    n, _, stats, _ = cv2.connectedComponentsWithStats(chars)
    blobs = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not max(8, 0.015 * h_img) <= h <= 0.3 * h_img:  # 멀리 있는 작은 숫자도 포함
            continue
        if not 0.12 <= w / float(h) <= 1.1:       # 숫자는 세로로 긴 모양 ('1'은 아주 가늚)
            continue
        if area < 0.15 * w * h:
            continue
        blobs.append((x, y, w, h))

    # 비슷한 높이 + 가까운 가로 간격 + 비슷한 세로 위치끼리 연결 (union-find)
    parent = list(range(len(blobs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, (x1, y1, w1, h1) in enumerate(blobs):
        for j in range(i + 1, len(blobs)):
            x2, y2, w2, h2 = blobs[j]
            if max(h1, h2) / float(min(h1, h2)) > 1.4:
                continue
            hm = (h1 + h2) / 2.0
            gap = max(x1, x2) - min(x1 + w1, x2 + w2)
            if gap > 0.9 * hm:
                continue
            if abs((y1 + h1 / 2.0) - (y2 + h2 / 2.0)) > 0.6 * hm:
                continue
            parent[find(i)] = find(j)

    groups = {}
    for i in range(len(blobs)):
        groups.setdefault(find(i), []).append(blobs[i])

    rows = [g for g in groups.values() if 3 <= len(g) <= 6]
    rows.sort(key=lambda g: (abs(len(g) - 4), -sum(b[3] for b in g)))
    return rows[:max_rows]


def _deskew_crop(img, row):
    """숫자 줄의 기울기만큼 영상을 돌려 똑바로 만든 뒤 번호판 부분만 잘라냄"""
    pts = []
    for x, y, w, h in row:
        pts += [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
    pts = np.array(pts, dtype=np.float32)
    # 각 숫자 중심을 잇는 직선의 기울기
    centers = np.array([(x + w / 2.0, y + h / 2.0) for x, y, w, h in row], dtype=np.float32)
    vx, vy, _, _ = cv2.fitLine(centers, cv2.DIST_L2, 0, 0.01, 0.01).flatten()
    angle = float(np.degrees(np.arctan2(vy, vx)))
    if angle > 45:
        angle -= 180
    elif angle < -45:
        angle += 180

    cx, cy = pts.mean(axis=0)
    rot = cv2.getRotationMatrix2D((float(cx), float(cy)), angle, 1.0)
    h_img, w_img = img.shape[:2]
    rotated = cv2.warpAffine(img, rot, (w_img, h_img), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    rp = cv2.transform(pts.reshape(-1, 1, 2), rot).reshape(-1, 2)
    x1, y1 = rp.min(axis=0)
    x2, y2 = rp.max(axis=0)
    ch = y2 - y1
    pad = 0.3 * ch
    x1, y1 = int(max(x1 - pad, 0)), int(max(y1 - pad, 0))
    x2, y2 = int(min(x2 + pad, w_img)), int(min(y2 + pad, h_img))
    return rotated[y1:y2, x1:x2]


def _white_boxes(gray):
    """흰 종이 번호판(밝은 직사각형) 찾기 (배경이 어두울 때 잘 동작)"""
    h_img, w_img = gray.shape[:2]
    _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    # RETR_LIST: 밝은 바닥·벽 안쪽(차 위)에 있는 번호판도 찾도록 모든 윤곽 검사
    contours, _ = cv2.findContours(bright, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < 400 or w * h > 0.6 * w_img * h_img:
            continue
        if not 1.2 <= w / float(h) <= 6.0:
            continue
        boxes.append((-w * h, x + 2, y + 2, w - 4, h - 4))
    boxes.sort()
    return [(x, y, w, h) for _, x, y, w, h in boxes]


def find_plate_candidates(img):
    """번호판일 가능성이 높은 영역을 잘라낸 이미지 목록 (가능성 높은 순)"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img

    # 1) 숫자 줄 기준 (기울기 보정 포함) — 대부분 여기서 찾음
    rows = []
    for binary in _char_binaries(gray):
        rows += _digit_rows(binary)
    # 번호판이 멀어서 숫자가 작으면, 2배로 키운 영상에서 한 번 더 찾기
    if not any(len(r) == 4 for r in rows):
        big = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        for binary in _char_binaries(big):
            rows += [[(x // 2, y // 2, max(w // 2, 1), max(h // 2, 1)) for x, y, w, h in r]
                     for r in _digit_rows(binary)]
    rows.sort(key=lambda g: (abs(len(g) - 4), -sum(b[3] for b in g)))
    picked = []
    for row in rows:  # 여러 이진 영상에서 같은 번호판을 찾았으면 하나만
        if not any(_overlaps(_row_box(row), _row_box(p)) for p in picked):
            picked.append(row)
    crops = [_deskew_crop(img, row) for row in picked[:3]]
    # 2) 숫자 줄을 못 찾았을 때만 흰 종이 기준으로
    if not crops:
        for x, y, w, h in _white_boxes(gray)[:2]:
            if w > 0 and h > 0:
                crops.append(img[y:y + h, x:x + w])
    return [c for c in crops if c.size]


# 같은 번호가 이만큼 나오면 남은 방법은 건너뜀 (속도 향상)
ENOUGH_VOTES = 3


def _collect_votes(img, votes, best_conf):
    """img를 여러 방법으로 읽어 4자리 결과별 득표수와 최고 conf를 누적.
    한 번호가 ENOUGH_VOTES표를 얻으면 True를 돌려주고 바로 멈춤"""
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
            if votes and max(votes.values()) >= ENOUGH_VOTES:
                return True
    return False


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
    crops = find_plate_candidates(img)
    for crop in crops:
        if _collect_votes(crop, votes, best_conf):
            break
    # 2) 번호판 후보를 하나도 못 찾았을 때만 이미지 전체로 시도
    #    (후보를 다 읽었는데 숫자가 없으면 번호판이 없는 것 → 빨리 다음 화면으로)
    if not crops:
        _collect_votes(img, votes, best_conf)
    return _score(votes, best_conf)
