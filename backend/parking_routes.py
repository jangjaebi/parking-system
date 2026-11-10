from datetime import datetime
from flask import Blueprint, jsonify, request
from database import get_db_connection

parking_bp = Blueprint('parking', __name__)

def get_now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

# 1. 전체 주차면 상태 조회 API
@parking_bp.route('/api/parking', methods=['GET'])
def get_parking_status():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                space_id,
                is_reserved,
                is_occupied,
                car_number,
                updated_at
            FROM parking_spaces
            ORDER BY space_id ASC
        ''')
        rows = cursor.fetchall()
        conn.close()

        spaces = []
        for row in rows:
            spaces.append({
                "space_id": row["space_id"],
                "is_reserved": bool(row["is_reserved"]),
                "is_occupied": bool(row["is_occupied"]),
                "car_number": row["car_number"],
                "updated_at": row["updated_at"]
            })

        return jsonify({
            "status": "success",
            "data": spaces
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"주차면 상태 조회 실패: {str(e)}"
        }), 500


# 2. 주차 예약 API (POST /api/reservations)
@parking_bp.route('/api/reservations', methods=['POST'])
def create_reservation():
    data = request.get_json() or {}
    space_id = data.get('space_id')
    car_number = data.get('car_number')

    if not space_id or not car_number:
        return jsonify({
            "status": "error",
            "message": "space_id와 car_number를 모두 입력해야 합니다."
        }), 400

    car_number = str(car_number).strip()
    if len(car_number) < 4:
        return jsonify({
            "status": "error",
            "message": "올바른 차량번호를 입력해주세요."
        }), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("BEGIN TRANSACTION")

        cursor.execute("SELECT is_reserved, is_occupied FROM parking_spaces WHERE space_id = ?", (space_id,))
        space = cursor.fetchone()

        if not space:
            conn.rollback()
            conn.close()
            return jsonify({"status": "error", "message": f"존재하지 않는 주차면입니다: {space_id}"}), 404

        if space["is_reserved"]:
            conn.rollback()
            conn.close()
            return jsonify({"status": "error", "message": f"이미 예약된 주차면입니다 ({space_id})."}), 409

        if space["is_occupied"]:
            conn.rollback()
            conn.close()
            return jsonify({"status": "error", "message": f"현재 차량이 주차되어 있어 예약할 수 없습니다 ({space_id})."}), 409

        cursor.execute("SELECT space_id FROM parking_spaces WHERE car_number = ? AND is_reserved = 1", (car_number,))
        existing_res = cursor.fetchone()
        if existing_res:
            conn.rollback()
            conn.close()
            return jsonify({"status": "error", "message": f"해당 차량({car_number})은 이미 {existing_res['space_id']} 구역에 예약되어 있습니다."}), 400

        now = get_now_str()
        cursor.execute('''
            UPDATE parking_spaces
            SET is_reserved = 1,
                car_number = ?,
                updated_at = ?
            WHERE space_id = ? AND is_reserved = 0
        ''', (car_number, now, space_id))

        conn.commit()
        conn.close()

        return jsonify({
            "status": "success",
            "message": f"{space_id} 주차면 예약이 완료되었습니다.",
            "data": {
                "space_id": space_id,
                "car_number": car_number,
                "updated_at": now
            }
        }), 201

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"status": "error", "message": f"예약 처리 중 오류 발생: {str(e)}"}), 500


# 3. 예약 취소 API (DELETE /api/reservations/<space_id>)
@parking_bp.route('/api/reservations/<space_id>', methods=['DELETE'])
def cancel_reservation(space_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT is_reserved, is_occupied FROM parking_spaces WHERE space_id = ?", (space_id,))
        space = cursor.fetchone()

        if not space:
            conn.close()
            return jsonify({"status": "error", "message": f"존재하지 않는 주차면입니다: {space_id}"}), 404

        if not space["is_reserved"]:
            conn.close()
            return jsonify({"status": "error", "message": f"{space_id} 주차면은 예약되어 있지 않습니다."}), 400

        now = get_now_str()
        cursor.execute('''
            UPDATE parking_spaces
            SET is_reserved = 0,
                car_number = NULL,
                updated_at = ?
            WHERE space_id = ?
        ''', (now, space_id))

        conn.commit()
        conn.close()

        return jsonify({
            "status": "success",
            "message": f"{space_id} 주차면의 예약이 취소되었습니다."
        }), 200

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"status": "error", "message": f"예약 취소 중 오류 발생: {str(e)}"}), 500


# 4. OpenCV 차량 점유 결과 수신 API (POST /api/occupancy)
@parking_bp.route('/api/occupancy', methods=['POST'])
def update_occupancy():
    """
    OpenCV 담당 팀원이 보낼 JSON 요청:
    { "space_id": "A1", "occupied": true }
    """
    data = request.get_json() or {}
    space_id = data.get('space_id')
    occupied = data.get('occupied')

    if space_id is None or occupied is None:
        return jsonify({"status": "error", "message": "space_id와 occupied(boolean) 필드가 필요합니다."}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        now = get_now_str()
        cursor.execute('''
            UPDATE parking_spaces
            SET is_occupied = ?,
                updated_at = ?
            WHERE space_id = ?
        ''', (1 if occupied else 0, now, space_id))

        if cursor.rowcount == 0:
            conn.close()
            return jsonify({"status": "error", "message": f"존재하지 않는 주차면입니다: {space_id}"}), 404

        conn.commit()
        conn.close()

        return jsonify({
            "status": "success",
            "message": f"{space_id} 구역 차량 점유 상태 업데이트 완료 ({occupied})",
            "data": {
                "space_id": space_id,
                "is_occupied": bool(occupied),
                "updated_at": now
            }
        }), 200

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"status": "error", "message": f"점유 상태 업데이트 오류: {str(e)}"}), 500


# 5. OCR 번호판 인식 결과 비교 API (POST /api/ocr)
@parking_bp.route('/api/ocr', methods=['POST'])
def verify_ocr():
    """
    OCR 담당 팀원이 보낼 JSON 요청:
    {
       "space_id": "A1",
       "detected_car_number": "12가3456",
       "confidence": 0.92  (신뢰도 0~1)
    }
    """
    data = request.get_json() or {}
    space_id = data.get('space_id')
    detected_car_number = data.get('detected_car_number')
    confidence = data.get('confidence', 1.0)  # 기본값 1.0

    if not space_id:
        return jsonify({"status": "error", "message": "space_id가 필요합니다."}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT is_reserved, car_number FROM parking_spaces WHERE space_id = ?", (space_id,))
        space = cursor.fetchone()
        conn.close()

        if not space:
            return jsonify({"status": "error", "message": f"존재하지 않는 주차면입니다: {space_id}"}), 404

        # 요구사항 5번: OCR 결과가 불확실한 경우(예: confidence < 0.70) 단정하지 않고 확인 필요 반환
        if confidence < 0.70 or not detected_car_number:
            return jsonify({
                "status": "warning",
                "result_code": "UNCERTAIN_OCR",
                "message": "인식 결과가 불확실합니다. 추가 확인이 필요합니다.",
                "data": {
                    "space_id": space_id,
                    "confidence": confidence
                }
            }), 200

        reserved_car_number = space["car_number"]

        # 예약 내역이 없는데 차가 인식된 경우
        if not space["is_reserved"]:
            return jsonify({
                "status": "warning",
                "result_code": "UNAUTHORIZED_PARKING",
                "message": f"미예약 주차 구역({space_id})에 차량({detected_car_number})이 주차되었습니다.",
                "data": {
                    "space_id": space_id,
                    "detected_car_number": detected_car_number
                }
            }), 200

        # 번호판 일치 여부 검사
        if reserved_car_number and reserved_car_number.strip() == detected_car_number.strip():
            return jsonify({
                "status": "success",
                "result_code": "MATCH",
                "message": "예약 차량번호와 정상 일치합니다.",
                "data": {
                    "space_id": space_id,
                    "car_number": detected_car_number
                }
            }), 200
        else:
            return jsonify({
                "status": "warning",
                "result_code": "MISMATCH",
                "message": f"차량번호 불일치 (예약: {reserved_car_number} / 인식: {detected_car_number})",
                "data": {
                    "space_id": space_id,
                    "reserved_car_number": reserved_car_number,
                    "detected_car_number": detected_car_number
                }
            }), 200

    except Exception as e:
        conn.close()
        return jsonify({"status": "error", "message": f"OCR 비교 오류: {str(e)}"}), 500