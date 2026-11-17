import re
import threading
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
from database import get_db_connection

parking_bp = Blueprint('parking', __name__)

VALID_SPACES = {'A1', 'A2', 'A3', 'A4', 'A5'}

# OCR 신뢰도 기준 (이 값 미만이면 '인식 불확실')
OCR_MIN_CONFIDENCE = 0.70

# 예약 후 이 시간(분) 안에 입구를 통과하지 않으면 예약 자동 취소 (0이면 사용 안 함)
RESERVATION_TIMEOUT_MINUTES = 30

TIME_FORMAT = '%Y-%m-%d %H:%M:%S'


def get_now_str():
    return datetime.now().strftime(TIME_FORMAT)


def normalize_car_number(car_number) -> str:
    """차량번호 공백 제거 및 문자열 변환 (예: ' 1234 ' -> '1234')"""
    return re.sub(r'\s+', '', str(car_number)).strip()


def is_valid_car_number(car_number: str) -> bool:
    """차량번호 형식 검증: 숫자 4자리만 허용 (예: '0123', '4567')"""
    return bool(re.fullmatch(r'\d{4}', car_number or ''))


def parse_confidence(value):
    """confidence가 없거나 숫자가 아니면 None (→ 400 처리). 값이 없을 때 '확실함(1.0)'으로 처리하지 않음"""
    try:
        conf = float(value)
    except (ValueError, TypeError):
        return None
    if not 0.0 <= conf <= 1.0:
        return None
    return conf


def error(message, code):
    return jsonify({"status": "error", "message": message}), code


# 예약을 초기 상태로 되돌리는 SQL (예약 취소 / 출차 / 시간 만료 시 공통 사용)
RELEASE_FIELDS = '''
    is_reserved = 0,
    car_number = NULL,
    entry_status = 'NONE',
    reserved_at = NULL
'''


def expire_reservations(cursor):
    """입구를 통과하지 않은 채 시간이 지난 예약을 자동 해제"""
    if RESERVATION_TIMEOUT_MINUTES <= 0:
        return []
    limit = (datetime.now() - timedelta(minutes=RESERVATION_TIMEOUT_MINUTES)).strftime(TIME_FORMAT)
    cursor.execute('''
        SELECT space_id FROM parking_spaces
        WHERE is_reserved = 1 AND entry_status = 'RESERVED' AND is_occupied = 0
          AND reserved_at IS NOT NULL AND reserved_at < ?
    ''', (limit,))
    expired = [row["space_id"] for row in cursor.fetchall()]
    for space_id in expired:
        cursor.execute(f'''
            UPDATE parking_spaces
            SET {RELEASE_FIELDS}, ocr_status = 'NONE', detected_car_number = NULL, updated_at = ?
            WHERE space_id = ?
        ''', (get_now_str(), space_id))
    return expired


# ──────────────────────────────────────────────
# 1. 전체 주차면 상태 조회 API (GET /api/parking)
# ──────────────────────────────────────────────
@parking_bp.route('/api/parking', methods=['GET'])
def get_parking_status():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        expire_reservations(cursor)
        conn.commit()

        cursor.execute('''
            SELECT space_id, is_reserved, is_occupied, car_number, entry_status,
                   reserved_at, ocr_status, detected_car_number, updated_at
            FROM parking_spaces
            ORDER BY space_id ASC
        ''')

        spaces = [{
            "space_id": row["space_id"],
            "is_reserved": bool(row["is_reserved"]),
            "is_occupied": bool(row["is_occupied"]),
            "car_number": row["car_number"],
            "entry_status": row["entry_status"] or "NONE",
            "reserved_at": row["reserved_at"],
            "ocr_status": row["ocr_status"],
            "detected_car_number": row["detected_car_number"],
            "updated_at": row["updated_at"]
        } for row in cursor.fetchall()]

        return jsonify({"status": "success", "data": spaces}), 200

    except Exception as e:
        return error(f"주차면 상태 조회 실패: {str(e)}", 500)
    finally:
        if conn:
            conn.close()


# ──────────────────────────────────────────────
# 2. 주차 예약 API (POST /api/reservations)
# ──────────────────────────────────────────────
@parking_bp.route('/api/reservations', methods=['POST'])
def create_reservation():
    data = request.get_json(silent=True) or {}
    space_id = str(data.get('space_id', '')).strip().upper()
    raw_car_number = data.get('car_number')

    if not space_id or raw_car_number is None or str(raw_car_number).strip() == '':
        return error("space_id와 car_number를 모두 입력해야 합니다.", 400)

    if space_id not in VALID_SPACES:
        return error(f"유효하지 않은 주차면 번호입니다: {space_id} (허용: A1~A5)", 400)

    car_number = normalize_car_number(raw_car_number)
    if not is_valid_car_number(car_number):
        return error("차량번호는 숫자 4자리여야 합니다. (예: 1234)", 400)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        expire_reservations(cursor)

        cursor.execute(
            "SELECT is_reserved, is_occupied FROM parking_spaces WHERE space_id = ?",
            (space_id,)
        )
        space = cursor.fetchone()

        if not space:
            conn.rollback()
            return error(f"존재하지 않는 주차면입니다: {space_id}", 404)

        if space["is_reserved"]:
            conn.rollback()
            return error(f"이미 예약된 주차면입니다 ({space_id}).", 409)

        if space["is_occupied"]:
            conn.rollback()
            return error(f"현재 차량이 주차되어 있어 예약할 수 없습니다 ({space_id}).", 409)

        cursor.execute(
            "SELECT space_id FROM parking_spaces WHERE car_number = ? AND is_reserved = 1",
            (car_number,)
        )
        existing_res = cursor.fetchone()
        if existing_res:
            conn.rollback()
            return error(f"해당 차량({car_number})은 이미 {existing_res['space_id']} 구역에 예약되어 있습니다.", 409)

        now = get_now_str()
        cursor.execute('''
            UPDATE parking_spaces
            SET is_reserved = 1,
                car_number = ?,
                entry_status = 'RESERVED',
                reserved_at = ?,
                ocr_status = 'NONE',
                detected_car_number = NULL,
                updated_at = ?
            WHERE space_id = ? AND is_reserved = 0
        ''', (car_number, now, now, space_id))

        if cursor.rowcount == 0:
            conn.rollback()
            return error("동시 요청으로 인해 예약에 실패했습니다. 다시 시도해주세요.", 409)

        conn.commit()

        return jsonify({
            "status": "success",
            "message": f"{space_id} 주차면 예약이 완료되었습니다.",
            "data": {
                "space_id": space_id,
                "car_number": car_number,
                "is_reserved": True,
                "entry_status": "RESERVED",
                "updated_at": now
            }
        }), 201

    except Exception as e:
        if conn:
            conn.rollback()
        return error(f"예약 처리 중 오류 발생: {str(e)}", 500)
    finally:
        if conn:
            conn.close()


# ──────────────────────────────────────────────
# 3. 예약 취소 API (DELETE /api/reservations/<space_id>)
#    입구를 통과했거나 주차 중이면 취소 불가
# ──────────────────────────────────────────────
@parking_bp.route('/api/reservations/<space_id>', methods=['DELETE'])
def cancel_reservation(space_id):
    space_id = space_id.strip().upper()

    if space_id not in VALID_SPACES:
        return error(f"유효하지 않은 주차면 번호입니다: {space_id}", 400)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        cursor.execute(
            "SELECT is_reserved, is_occupied, entry_status FROM parking_spaces WHERE space_id = ?",
            (space_id,)
        )
        space = cursor.fetchone()

        if not space:
            conn.rollback()
            return error(f"존재하지 않는 주차면입니다: {space_id}", 404)

        if not space["is_reserved"]:
            conn.rollback()
            return error(f"{space_id} 주차면은 예약되어 있지 않습니다.", 400)

        if space["is_occupied"] or space["entry_status"] in ('ENTERED', 'PARKED'):
            conn.rollback()
            return error(f"이미 입차한 예약은 취소할 수 없습니다 ({space_id}).", 409)

        now = get_now_str()
        cursor.execute(f'''
            UPDATE parking_spaces
            SET {RELEASE_FIELDS}, ocr_status = 'NONE', detected_car_number = NULL, updated_at = ?
            WHERE space_id = ?
        ''', (now, space_id))

        conn.commit()

        return jsonify({
            "status": "success",
            "message": f"{space_id} 주차면의 예약이 취소되었습니다.",
            "data": {"space_id": space_id, "is_reserved": False, "updated_at": now}
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return error(f"예약 취소 중 오류 발생: {str(e)}", 500)
    finally:
        if conn:
            conn.close()


# ──────────────────────────────────────────────
# 4. 상단 카메라 점유 결과 수신 API (POST /api/occupancy)
#    - 점유 + 미예약 칸 → UNAUTHORIZED(무단 주차)
#    - 점유 + 입구 통과한 예약 칸 → PARKED
#    - PARKED 칸이 비면 → 출차로 보고 예약 자동 해제
# ──────────────────────────────────────────────
@parking_bp.route('/api/occupancy', methods=['POST'])
def update_occupancy():
    data = request.get_json(silent=True) or {}
    space_id = str(data.get('space_id', '')).strip().upper()
    occupied = data.get('occupied')

    if not space_id or not isinstance(occupied, bool):
        return error("space_id(문자열)와 occupied(true/false 불리언) 필드가 필요합니다.", 400)

    if space_id not in VALID_SPACES:
        return error(f"유효하지 않은 주차면 번호입니다: {space_id}", 400)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        cursor.execute(
            "SELECT is_reserved, entry_status FROM parking_spaces WHERE space_id = ?",
            (space_id,)
        )
        space = cursor.fetchone()
        if not space:
            conn.rollback()
            return error(f"존재하지 않는 주차면입니다: {space_id}", 404)

        now = get_now_str()
        entry_status = space["entry_status"] or "NONE"
        released = False

        if occupied:
            if not space["is_reserved"]:
                # 예약 없는 칸에 차량 → 무단 주차
                ocr_status = 'UNAUTHORIZED'
                new_entry = entry_status
            else:
                # 예약 칸에 차량 → 번호판 대조 결과(/api/ocr) 대기
                ocr_status = 'NONE'
                new_entry = 'PARKED' if entry_status == 'ENTERED' else entry_status

            cursor.execute('''
                UPDATE parking_spaces
                SET is_occupied = 1, entry_status = ?, ocr_status = ?, detected_car_number = NULL, updated_at = ?
                WHERE space_id = ?
            ''', (new_entry, ocr_status, now, space_id))
            message = f"{space_id} 구역 차량 감지"
        else:
            if entry_status == 'PARKED':
                # 예약 차량이 주차했다가 빠져나감 → 예약 종료
                cursor.execute(f'''
                    UPDATE parking_spaces
                    SET is_occupied = 0, {RELEASE_FIELDS}, ocr_status = 'NONE',
                        detected_car_number = NULL, updated_at = ?
                    WHERE space_id = ?
                ''', (now, space_id))
                released = True
                message = f"{space_id} 구역 출차 확인 → 예약이 자동 종료되었습니다."
            else:
                cursor.execute('''
                    UPDATE parking_spaces
                    SET is_occupied = 0, ocr_status = 'NONE', detected_car_number = NULL, updated_at = ?
                    WHERE space_id = ?
                ''', (now, space_id))
                message = f"{space_id} 구역 빈칸 감지"

        conn.commit()

        return jsonify({
            "status": "success",
            "message": message,
            "data": {
                "space_id": space_id,
                "is_occupied": occupied,
                "reservation_released": released,
                "updated_at": now
            }
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return error(f"점유 상태 업데이트 오류: {str(e)}", 500)
    finally:
        if conn:
            conn.close()


# ──────────────────────────────────────────────
# 5. 상단 카메라 번호판 대조 API (POST /api/ocr)
#    차 지붕의 번호판을 읽어 예약 차량이 맞는 칸에 섰는지 확인
# ──────────────────────────────────────────────
@parking_bp.route('/api/ocr', methods=['POST'])
def verify_ocr():
    data = request.get_json(silent=True) or {}
    space_id = str(data.get('space_id', '')).strip().upper()
    raw_detected = data.get('detected_car_number')
    confidence = parse_confidence(data.get('confidence'))

    if not space_id:
        return error("space_id가 필요합니다.", 400)

    if space_id not in VALID_SPACES:
        return error(f"유효하지 않은 주차면 번호입니다: {space_id}", 400)

    if confidence is None:
        return error("confidence(0.0~1.0 숫자)가 필요합니다.", 400)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        cursor.execute(
            "SELECT is_reserved, is_occupied, car_number, entry_status FROM parking_spaces WHERE space_id = ?",
            (space_id,)
        )
        space = cursor.fetchone()

        if not space:
            conn.rollback()
            return error(f"존재하지 않는 주차면입니다: {space_id}", 404)

        now = get_now_str()
        detected = normalize_car_number(raw_detected) if raw_detected else None
        reserved_car_number = space["car_number"]
        entry_status = space["entry_status"] or "NONE"

        if confidence < OCR_MIN_CONFIDENCE or not is_valid_car_number(detected):
            result, status = "UNCERTAIN", "warning"
            message = "번호판(숫자 4자리) 인식 결과가 불확실합니다. 확인이 필요합니다."
        elif not space["is_reserved"]:
            result, status = "UNAUTHORIZED", "warning"
            message = f"미예약 구역({space_id})에 차량({detected})이 주차되어 있습니다."
        elif detected == normalize_car_number(reserved_car_number):
            result, status = "MATCH", "success"
            message = "예약 차량번호와 일치합니다."
            # 예약 차량이 제자리에 주차한 것이 확인됨
            if space["is_occupied"]:
                entry_status = 'PARKED'
        else:
            result, status = "MISMATCH", "warning"
            message = f"차량번호 불일치 (예약: {reserved_car_number} / 인식: {detected})"
            # 다른 차가 서 있는 것이므로, 이 차가 나가도 예약이 풀리지 않도록 PARKED 해제
            if entry_status == 'PARKED':
                entry_status = 'ENTERED'

        cursor.execute('''
            UPDATE parking_spaces
            SET ocr_status = ?, detected_car_number = ?, entry_status = ?, updated_at = ?
            WHERE space_id = ?
        ''', (result, detected, entry_status, now, space_id))
        conn.commit()

        return jsonify({
            "status": status,
            "result_code": result,
            "message": message,
            "data": {
                "space_id": space_id,
                "reserved_car_number": reserved_car_number,
                "detected_car_number": detected,
                "confidence": confidence
            }
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return error(f"OCR 비교 처리 오류: {str(e)}", 500)
    finally:
        if conn:
            conn.close()


# ──────────────────────────────────────────────
# 입구 차단봉 상태 (서버 메모리에 저장)
#   entry_status: waiting(대기) / authorized(입차 승인) / denied(거부) / entered(통과 후 닫힘)
# ──────────────────────────────────────────────
GATE_LOCK = threading.Lock()
GATE_STATE = {
    "status": "CLOSED",
    "last_car_number": None,
    "match": None,
    "authorized_space": None,
    "entry_status": "waiting",
    "message": "차량 대기 중",
    "updated_at": None
}


def set_gate_state(**fields):
    with GATE_LOCK:
        GATE_STATE.update(fields, updated_at=get_now_str())
        return dict(GATE_STATE)


# ──────────────────────────────────────────────
# 6. 입구 OCR 검증 및 차단봉 개방 판단 API (POST /api/gate/entry)
#    { "detected_car_number": "1234", "confidence": 0.95 }
# ──────────────────────────────────────────────
@parking_bp.route('/api/gate/entry', methods=['POST'])
def check_gate_entry():
    data = request.get_json(silent=True) or {}
    raw_detected = data.get('detected_car_number')
    confidence = parse_confidence(data.get('confidence'))

    if confidence is None:
        return error("confidence(0.0~1.0 숫자)가 필요합니다.", 400)

    detected = normalize_car_number(raw_detected) if raw_detected else None

    def keep_closed(message, match):
        set_gate_state(status="CLOSED", last_car_number=detected, match=match,
                       authorized_space=None, entry_status="denied", message=message)
        return jsonify({
            "status": "warning",
            "gate_action": "KEEP_CLOSED",
            "message": message,
            "data": {"detected_car_number": detected, "confidence": confidence, "gate_status": "CLOSED"}
        }), 200

    # 1) 인식 불확실 또는 숫자 4자리가 아님
    if confidence < OCR_MIN_CONFIDENCE or not is_valid_car_number(detected):
        return keep_closed("번호판 인식이 불확실하여 차단봉을 열지 않습니다.", None)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        expire_reservations(cursor)

        cursor.execute(
            "SELECT space_id, entry_status FROM parking_spaces WHERE car_number = ? AND is_reserved = 1",
            (detected,)
        )
        reserved_space = cursor.fetchone()

        # 2) 예약 없는 차량
        if not reserved_space:
            conn.commit()
            return keep_closed(f"미예약 차량({detected})입니다. 진입이 제한됩니다.", False)

        # 3) 이미 입구를 통과한 예약 (같은 예약으로 중복 입차 방지)
        if reserved_space["entry_status"] in ('ENTERED', 'PARKED'):
            conn.commit()
            with GATE_LOCK:
                passing_now = (GATE_STATE["entry_status"] == "authorized"
                               and GATE_STATE["last_car_number"] == detected)
            if passing_now:
                # 방금 승인된 차량이 아직 차단봉 아래를 지나는 중 → 상태를 덮어쓰지 않음
                return jsonify({
                    "status": "success",
                    "gate_action": "ALREADY_OPEN",
                    "message": f"{detected} 차량이 통과 중입니다.",
                    "data": {"detected_car_number": detected, "gate_status": "OPEN"}
                }), 200
            return keep_closed(f"이미 입차 처리된 차량({detected})입니다.", True)

        # 4) 정상 예약 차량 → 입차 처리 후 차단봉 개방
        space_id = reserved_space["space_id"]
        cursor.execute('''
            UPDATE parking_spaces SET entry_status = 'ENTERED', updated_at = ?
            WHERE space_id = ?
        ''', (get_now_str(), space_id))
        conn.commit()

        message = f"예약 확인 완료 ({space_id} 구역). 입구 차단봉을 엽니다."
        set_gate_state(status="OPEN", last_car_number=detected, match=True,
                       authorized_space=space_id, entry_status="authorized", message=message)

        return jsonify({
            "status": "success",
            "gate_action": "OPEN",
            "message": message,
            "data": {"space_id": space_id, "car_number": detected, "gate_status": "OPEN"}
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return error(f"입구 검증 오류: {str(e)}", 500)
    finally:
        if conn:
            conn.close()


# ──────────────────────────────────────────────
# 7. 입구 차단봉 상태 조회/닫기 API (GET/POST /api/gate/status)
#    POST는 닫기(CLOSED)만 허용 — 열기는 /api/gate/entry 판정으로만 가능
# ──────────────────────────────────────────────
@parking_bp.route('/api/gate/status', methods=['GET', 'POST'])
def manage_gate_status():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        action = str(data.get('status', '')).strip().upper()
        if action != 'CLOSED':
            return error("차단봉은 닫기(CLOSED)만 요청할 수 있습니다.", 400)

        with GATE_LOCK:
            was_authorized = GATE_STATE["entry_status"] == "authorized"
        if was_authorized:
            set_gate_state(status="CLOSED", entry_status="entered", message="차량 통과 후 차단봉이 닫혔습니다.")
        else:
            set_gate_state(status="CLOSED")

    with GATE_LOCK:
        state = dict(GATE_STATE)
    return jsonify({"status": "success", "data": state}), 200
