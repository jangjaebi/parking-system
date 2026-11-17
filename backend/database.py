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
    # entry_status            : 예약 진행 단계
    #     NONE(예약 없음) → RESERVED(예약됨) → ENTERED(입구 통과) → PARKED(주차 완료)
    #     PARKED 상태에서 빈칸이 감지되면(출차) 예약이 자동 해제됨
    # ocr_status              : 상단 카메라 번호판 대조 결과
    #     NONE(미확인), MATCH(일치), MISMATCH(불일치), UNCERTAIN(인식 불확실), UNAUTHORIZED(미예약 칸 주차)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parking_spaces (
            space_id TEXT PRIMARY KEY,
            is_reserved INTEGER DEFAULT 0,
            is_occupied INTEGER DEFAULT 0,
            car_number TEXT,
            entry_status TEXT DEFAULT 'NONE',
            reserved_at TIMESTAMP,
            ocr_status TEXT DEFAULT 'NONE',
            detected_car_number TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    existing = {row["name"] for row in cursor.execute("PRAGMA table_info(parking_spaces)")}
    for column, definition in EXTRA_COLUMNS.items():
        if column not in existing:
            cursor.execute(f"ALTER TABLE parking_spaces ADD COLUMN {column} {definition}")
    # 예전 DB에서 예약되어 있던 칸은 RESERVED 단계로 맞춤
    cursor.execute('''
        UPDATE parking_spaces SET entry_status = 'RESERVED'
        WHERE is_reserved = 1 AND (entry_status IS NULL OR entry_status = 'NONE')
    ''')

    for space in SPACES:
        cursor.execute('''
            INSERT OR IGNORE INTO parking_spaces
            (space_id, is_reserved, is_occupied, car_number, entry_status, ocr_status, detected_car_number)
            VALUES (?, 0, 0, NULL, 'NONE', 'NONE', NULL)
        ''', (space,))

    conn.commit()
    conn.close()
    print(f"Database initialized successfully at: {DB_PATH}")
