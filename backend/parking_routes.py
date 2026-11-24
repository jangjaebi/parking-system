import re
import threading
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
from database import get_db_connection

parking_bp = Blueprint('parking', __name__)

VALID_SPACES = {'A1', 'A2', 'A3', 'A4', 'A5'}

# 입구 OCR 신뢰도 기준 (이 값 미만이면 '인식 불확실')
OCR_MIN_CONFIDENCE = 0.70

# 예약 후 이 시간(분) 안에 입구 통과·주차 시작을 하지 않으면 예약 자동 취소 (0이면 사용 안 함)
RESERVATION_TIMEOUT_MINUTES = 30

# '주차 완료'를 눌렀는데 칸이 비어 있을 때 앱에 경고를 표시하는 시간(초)
WRONG_SPOT_ALARM_SECONDS = 10

TIME_FORMAT = '%Y-%m-%d %H:%M:%S'

ALARM_MESSAGES = {
    "UNAUTHORIZED": "예약된 자리에 '주차하기' 없이 차량이 들어왔습니다.",
    "WRONG_SPOT": "주차가 완료되지 않았습니다. 자리를 확인해주세요.",
}


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


def error(message, code, **extra):
    body = {"status": "error", "message": message}
    body.update(extra)
    return jsonify(body), code


# 예약을 초기 상태로 되돌리는 SQL (예약 취소 / 출차 / 시간 만료 시 공통 사용)
RELEASE_FIELDS = '''
    is_reserved = 0,
    car_number = NULL,
    entry_status = 'NONE',
    reserved_at = NULL,
    entered = 0,
    alarm = 'NONE',
    alarm_until = NULL
'''


def expire_reservations(cursor):
    """입구 통과·주차 시작 없이 시간이 지난 예약을 자동 해제"""
    if RESERVATION_TIMEOUT_MINUTES <= 0:
        return []
    limit = (datetime.now() - timedelta(minutes=RESERVATION_TIMEOUT_MINUTES)).strftime(TIME_FORMAT)
    cursor.execute('''
        SELECT space_id FROM parking_spaces
        WHERE is_reserved = 1 AND entry_status = 'RESERVED' AND entered = 0 AND is_occupied = 0
          AND reserved_at IS NOT NULL AND reserved_at < ?
    ''', (limit,))
    expired = [row["space_id"] for row in cursor.fetchall()]
    for space_id in expired:
        cursor.execute(f'''
            UPDATE parking_spaces SET {RELEASE_FIELDS}, updated_at = ?
            WHERE space_id = ?
        ''', (get_now_str(), space_id))
    return expired


def clear_finished_alarms(cursor):
    """시간이 정해진 경고(WRONG_SPOT)가 끝났으면 해제"""
    cursor.execute('''
        UPDATE parking_spaces SET alarm = 'NONE', alarm_until = NULL
        WHERE alarm_until IS NOT NULL AND alarm_until <= ?
    ''', (get_now_str(),))


def refresh_state(cursor):
    expire_reservations(cursor)
    clear_finished_alarms(cursor)


def check_owner(space, car_number):
    """주차하기/주차 완료 요청이 예약한 차량번호와 같은지 확인. 문제 있으면 오류 응답, 없으면 None"""
    if not space:
        return error("존재하지 않는 주차면입니다.", 404)
    if not space["is_reserved"]:
        return error("예약되어 있지 않은 주차면입니다.", 400)
    if normalize_car_number(space["car_number"]) != car_number:
        return error("예약한 차량번호와 일치하지 않습니다.", 403)
    return None


# ──────────────────────────────────────────────
# 1. 전체 주차면 상태 조회 API (GET /api/parking)
# ──────────────────────────────────────────────
@parking_bp.route('/api/parking', methods=['GET'])
def get_parking_status():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        refresh_state(cursor)
        conn.commit()

        cursor.execute('''
            SELECT space_id, is_reserved, is_occupied, car_number, entry_status,
                   reserved_at, entered, alarm, updated_at
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
            "entered": bool(row["entered"]),
            "alarm": row["alarm"] or "NONE",
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

        refresh_state(cursor)

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
                entered = 0,
                alarm = 'NONE',
                alarm_until = NULL,
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
#    입구를 통과했거나 주차를 시작했으면 취소 불가
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
            "SELECT is_reserved, is_occupied, entry_status, entered FROM parking_spaces WHERE space_id = ?",
            (space_id,)
        )
        space = cursor.fetchone()

        if not space:
            conn.rollback()
            return error(f"존재하지 않는 주차면입니다: {space_id}", 404)

        if not space["is_reserved"]:
            conn.rollback()
            return error(f"{space_id} 주차면은 예약되어 있지 않습니다.", 400)

        if space["entered"] or space["entry_status"] in ('PARKING', 'PARKED'):
            conn.rollback()
            return error(f"입차했거나 주차를 시작한 예약은 취소할 수 없습니다 ({space_id}).", 409)

        if space["is_occupied"]:
            conn.rollback()
            return error(f"자리에 차량이 감지되어 취소할 수 없습니다 ({space_id}).", 409)

        now = get_now_str()
        cursor.execute(f'''
            UPDATE parking_spaces SET {RELEASE_FIELDS}, updated_at = ?
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
# 4. 주차하기 API (POST /api/reservations/<space_id>/start)
#    { "car_number": "1234" }
#    차를 칸에 대기 '전에' 앱에서 누름 → PARKING(주차중)
# ──────────────────────────────────────────────
@parking_bp.route('/api/reservations/<space_id>/start', methods=['POST'])
def start_parking(space_id):
    space_id = space_id.strip().upper()
    data = request.get_json(silent=True) or {}
    car_number = normalize_car_number(data.get('car_number', ''))

    if space_id not in VALID_SPACES:
        return error(f"유효하지 않은 주차면 번호입니다: {space_id}", 400)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        refresh_state(cursor)

        cursor.execute(
            "SELECT is_reserved, is_occupied, car_number, entry_status FROM parking_spaces WHERE space_id = ?",
            (space_id,)
        )
        space = cursor.fetchone()
        problem = check_owner(space, car_number)
        if problem:
            conn.rollback()
            return problem

        if space["entry_status"] == 'PARKING':
            conn.commit()
            return jsonify({"status": "success", "message": "이미 주차중입니다.",
                            "data": {"space_id": space_id, "entry_status": "PARKING"}}), 200

        if space["entry_status"] == 'PARKED':
            conn.rollback()
            return error("이미 주차가 완료된 예약입니다.", 409)

        # 누르기 전에 이미 차가 있으면 → 다른 차가 먼저 댄 것 (경고 유지)
        if space["is_occupied"]:
            conn.rollback()
            return error(f"{space_id} 자리에 이미 차량이 있습니다. 자리를 확인해주세요.", 409)

        cursor.execute('''
            UPDATE parking_spaces SET entry_status = 'PARKING', updated_at = ?
            WHERE space_id = ?
        ''', (get_now_str(), space_id))
        conn.commit()

        return jsonify({
            "status": "success",
            "message": f"{space_id} 자리에 주차를 시작하세요. 주차 후 '주차 완료'를 눌러주세요.",
            "data": {"space_id": space_id, "entry_status": "PARKING"}
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return error(f"주차 시작 처리 오류: {str(e)}", 500)
    finally:
        if conn:
            conn.close()


# ──────────────────────────────────────────────
# 5. 주차 완료 API (POST /api/reservations/<space_id>/complete)
#    { "car_number": "1234" }
#    칸에 차가 감지되어 있으면 PARKED(주차 완료)
#    칸이 비어 있으면 → 자리 착각으로 보고 앱 경고(WRONG_SPOT) + 409
# ──────────────────────────────────────────────
@parking_bp.route('/api/reservations/<space_id>/complete', methods=['POST'])
def complete_parking(space_id):
    space_id = space_id.strip().upper()
    data = request.get_json(silent=True) or {}
    car_number = normalize_car_number(data.get('car_number', ''))

    if space_id not in VALID_SPACES:
        return error(f"유효하지 않은 주차면 번호입니다: {space_id}", 400)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        refresh_state(cursor)

        cursor.execute(
            "SELECT is_reserved, is_occupied, car_number, entry_status FROM parking_spaces WHERE space_id = ?",
            (space_id,)
        )
        space = cursor.fetchone()
        problem = check_owner(space, car_number)
        if problem:
            conn.rollback()
            return problem

        if space["entry_status"] == 'PARKED':
            conn.commit()
            return jsonify({"status": "success", "message": "이미 주차가 완료되었습니다.",
                            "data": {"space_id": space_id, "entry_status": "PARKED"}}), 200

        if space["entry_status"] != 'PARKING':
            conn.rollback()
            return error("먼저 '주차하기'를 눌러주세요.", 409)

        now = get_now_str()
        if not space["is_occupied"]:
            until = (datetime.now() + timedelta(seconds=WRONG_SPOT_ALARM_SECONDS)).strftime(TIME_FORMAT)
            cursor.execute('''
                UPDATE parking_spaces SET alarm = 'WRONG_SPOT', alarm_until = ?, updated_at = ?
                WHERE space_id = ?
            ''', (until, now, space_id))
            conn.commit()
            return error(ALARM_MESSAGES["WRONG_SPOT"], 409, alarm="WRONG_SPOT", space_id=space_id)

        cursor.execute('''
            UPDATE parking_spaces SET entry_status = 'PARKED', alarm = 'NONE', alarm_until = NULL, updated_at = ?
            WHERE space_id = ?
        ''', (now, space_id))
        conn.commit()

        return jsonify({
            "status": "success",
            "message": f"{space_id} 주차가 완료되었습니다.",
            "data": {"space_id": space_id, "entry_status": "PARKED"}
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return error(f"주차 완료 처리 오류: {str(e)}", 500)
    finally:
        if conn:
            conn.close()


# ──────────────────────────────────────────────
# 6-1. 주차 종료 API (POST /api/reservations/<space_id>/end)
#    { "car_number": "1234" }
#    예약을 끝내는 유일한 방법 (입차 전이면 '예약 취소' 사용)
#    차가 아직 칸에 있어도 종료 가능 — 그 칸은 차가 빠질 때까지 새로 예약할 수 없음
# ──────────────────────────────────────────────
@parking_bp.route('/api/reservations/<space_id>/end', methods=['POST'])
def end_parking(space_id):
    space_id = space_id.strip().upper()
    data = request.get_json(silent=True) or {}
    car_number = normalize_car_number(data.get('car_number', ''))

    if space_id not in VALID_SPACES:
        return error(f"유효하지 않은 주차면 번호입니다: {space_id}", 400)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        refresh_state(cursor)

        cursor.execute(
            "SELECT is_reserved, is_occupied, car_number, entry_status, entered FROM parking_spaces WHERE space_id = ?",
            (space_id,)
        )
        space = cursor.fetchone()
        problem = check_owner(space, car_number)
        if problem:
            conn.rollback()
            return problem

        if space["entry_status"] == 'RESERVED' and not space["entered"]:
            conn.rollback()
            return error("아직 입차하지 않은 예약입니다. '예약 취소'를 이용해주세요.", 409)

        cursor.execute(f'''
            UPDATE parking_spaces SET {RELEASE_FIELDS}, updated_at = ?
            WHERE space_id = ?
        ''', (get_now_str(), space_id))
        conn.commit()

        return jsonify({
            "status": "success",
            "message": f"{space_id} 주차가 종료되었습니다. 이용해 주셔서 감사합니다.",
            "data": {"space_id": space_id, "is_reserved": False}
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return error(f"주차 종료 처리 오류: {str(e)}", 500)
    finally:
        if conn:
            conn.close()


# ──────────────────────────────────────────────
# 6. 상단 카메라 점유 결과 수신 API (POST /api/occupancy)
#    { "space_id": "A1", "occupied": true }
#    - 예약 칸인데 '주차하기'를 안 누른 상태에서 차 감지 → 앱 경고(UNAUTHORIZED), 차가 빠질 때까지
#    - PARKING(주차중) 칸에 차 감지 → 정상 (자리 확인 경고가 있었다면 해제)
#    - 차가 빠져도 예약은 끝나지 않음 (자리 맞추느라 들락날락할 수 있음) → 앱에서 '주차 종료'로만 끝남
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
        refresh_state(cursor)

        cursor.execute(
            "SELECT is_reserved, entry_status, alarm FROM parking_spaces WHERE space_id = ?",
            (space_id,)
        )
        space = cursor.fetchone()
        if not space:
            conn.rollback()
            return error(f"존재하지 않는 주차면입니다: {space_id}", 404)

        now = get_now_str()
        entry_status = space["entry_status"] or "NONE"
        alarm = space["alarm"] or "NONE"
        if occupied:
            if space["is_reserved"] and entry_status == 'RESERVED':
                # 예약된 자리에 '주차하기' 없이 차가 들어옴 → 경고
                cursor.execute('''
                    UPDATE parking_spaces SET is_occupied = 1, alarm = 'UNAUTHORIZED', alarm_until = NULL, updated_at = ?
                    WHERE space_id = ?
                ''', (now, space_id))
                message = f"{space_id} 경고: {ALARM_MESSAGES['UNAUTHORIZED']}"
            elif entry_status == 'PARKING':
                # 예약자가 주차중 → 정상. 자리 확인 경고가 있었다면 해제
                cursor.execute('''
                    UPDATE parking_spaces SET is_occupied = 1, alarm = 'NONE', alarm_until = NULL, updated_at = ?
                    WHERE space_id = ?
                ''', (now, space_id))
                message = f"{space_id} 예약 차량 주차 감지 (앱에서 '주차 완료'를 눌러주세요)"
            else:
                cursor.execute('''
                    UPDATE parking_spaces SET is_occupied = 1, updated_at = ?
                    WHERE space_id = ?
                ''', (now, space_id))
                message = (f"{space_id} 차량 감지" if space["is_reserved"]
                           else f"{space_id} 예약 없는 자리에 차량 감지")
        else:
            # 무단 주차 차량이 빠지면 경고 해제 (자리 확인 경고는 정해진 시간 동안 유지)
            new_alarm = 'NONE' if alarm == 'UNAUTHORIZED' else alarm
            cursor.execute('''
                UPDATE parking_spaces SET is_occupied = 0, alarm = ?, updated_at = ?
                WHERE space_id = ?
            ''', (new_alarm, now, space_id))
            message = f"{space_id} 빈칸 감지"

        conn.commit()

        cursor = conn.cursor()
        cursor.execute("SELECT alarm FROM parking_spaces WHERE space_id = ?", (space_id,))
        current_alarm = cursor.fetchone()["alarm"] or "NONE"

        return jsonify({
            "status": "success",
            "message": message,
            "data": {
                "space_id": space_id,
                "is_occupied": occupied,
                "alarm": current_alarm,
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
# 7. 입구 OCR 검증 및 차단봉 개방 판단 API (POST /api/gate/entry)
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

        refresh_state(cursor)

        cursor.execute(
            "SELECT space_id, entered FROM parking_spaces WHERE car_number = ? AND is_reserved = 1",
            (detected,)
        )
        reserved_space = cursor.fetchone()

        # 2) 예약 없는 차량
        if not reserved_space:
            conn.commit()
            return keep_closed(f"미예약 차량({detected})입니다. 진입이 제한됩니다.", False)

        # 3) 이미 입구를 통과한 예약 (같은 예약으로 중복 입차 방지)
        if reserved_space["entered"]:
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
            UPDATE parking_spaces SET entered = 1, updated_at = ?
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
# 8. 입구 차단봉 상태 조회/닫기 API (GET/POST /api/gate/status)
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
