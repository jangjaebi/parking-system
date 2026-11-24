import os
import sqlite3

# 실행 위치에 상관없이 항상 backend/parking.db를 가리키도록 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "parking.db")

SPACES = ['A1', 'A2', 'A3', 'A4', 'A5']

# 예전 버전 DB에 없던 컬럼 → 자동으로 추가 (기존 parking.db를 지우지 않아도 됨)
EXTRA_COLUMNS = {
    "entry_status": "TEXT DEFAULT 'NONE'",
    "reserved_at": "TIMESTAMP",
    "ocr_status": "TEXT DEFAULT 'NONE'",
    "detected_car_number": "TEXT",
    "entered": "INTEGER DEFAULT 0",
    "alarm": "TEXT DEFAULT 'NONE'",
    "alarm_until": "TIMESTAMP",
}


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # 동시 읽기/쓰기 성능 향상 및 잠금 최소화
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # is_reserved / car_number : 예약 여부와 예약 차량번호(숫자 4자리)
    # is_occupied             : 상단 카메라가 감지한 점유 여부
    # entered                 : 입구 차단봉을 통과했는지 (같은 예약으로 두 번 들어오는 것 방지)
    # entry_status            : 주차 진행 단계
    #     NONE(예약 없음) → RESERVED(예약됨) → PARKING(앱에서 '주차하기' 누름) → PARKED(앱에서 '주차 완료' 누름)
    #     예약은 앱에서 '주차 종료'를 눌러야 끝남 (차가 칸을 들락날락해도 유지)
    # alarm / alarm_until     : 앱 경고 표시
    #     UNAUTHORIZED(예약 칸에 '주차하기' 없이 차가 들어옴, 차가 빠질 때까지 계속)
    #     WRONG_SPOT('주차 완료'를 눌렀는데 칸이 비어 있음, alarm_until까지)
    # ocr_status / detected_car_number : 예전 버전 호환용 (현재 사용 안 함)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parking_spaces (
            space_id TEXT PRIMARY KEY,
            is_reserved INTEGER DEFAULT 0,
            is_occupied INTEGER DEFAULT 0,
            car_number TEXT,
            entry_status TEXT DEFAULT 'NONE',
            reserved_at TIMESTAMP,
            entered INTEGER DEFAULT 0,
            alarm TEXT DEFAULT 'NONE',
            alarm_until TIMESTAMP,
            ocr_status TEXT DEFAULT 'NONE',
            detected_car_number TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    existing = {row["name"] for row in cursor.execute("PRAGMA table_info(parking_spaces)")}
    for column, definition in EXTRA_COLUMNS.items():
        if column not in existing:
            cursor.execute(f"ALTER TABLE parking_spaces ADD COLUMN {column} {definition}")

    # 예전 DB 값 정리
    #  - 예약되어 있는데 단계가 비어 있으면 RESERVED
    #  - 예전 단계 ENTERED(입구 통과)는 RESERVED + entered=1 로 변환
    cursor.execute('''
        UPDATE parking_spaces SET entry_status = 'RESERVED'
        WHERE is_reserved = 1 AND (entry_status IS NULL OR entry_status = 'NONE')
    ''')
    cursor.execute('''
        UPDATE parking_spaces SET entry_status = 'RESERVED', entered = 1
        WHERE entry_status = 'ENTERED'
    ''')
    cursor.execute("UPDATE parking_spaces SET alarm = 'NONE' WHERE alarm IS NULL")
    cursor.execute("UPDATE parking_spaces SET entered = 0 WHERE entered IS NULL")

    for space in SPACES:
        cursor.execute('''
            INSERT OR IGNORE INTO parking_spaces (space_id) VALUES (?)
        ''', (space,))

    conn.commit()
    conn.close()
    print(f"Database initialized successfully at: {DB_PATH}")
