import sqlite3

DB_NAME = "parking.db"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # barrier_lowered 컬럼 제거
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parking_spaces (
            space_id TEXT PRIMARY KEY,
            is_reserved INTEGER DEFAULT 0,
            is_occupied INTEGER DEFAULT 0,
            car_number TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    spaces = ['A1', 'A2', 'A3', 'A4', 'A5']
    for space in spaces:
        cursor.execute('''
            INSERT OR IGNORE INTO parking_spaces 
            (space_id, is_reserved, is_occupied, car_number)
            VALUES (?, 0, 0, NULL)
        ''', (space,))

    conn.commit()
    conn.close()
    print("Database initialized successfully without barriers (A1-A5).")