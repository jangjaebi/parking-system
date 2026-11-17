
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

from database import init_db
from parking_routes import parking_bp


def create_app():
    app = Flask(__name__)

    FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

    # React 연동 CORS 전체 허용
    CORS(app, resources={
        r"/api/*": {
            "origins": "*",
            "methods": ["GET", "POST", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"]
        }
    })

    # DB 초기화 및 5개 주차면 세팅
    init_db()

    # API 라우트 등록
    app.register_blueprint(parking_bp)

    @app.route("/api/health", methods=["GET"])
    def health_check():
        return jsonify({
            "status": "success",
            "message": "Smart Parking System API Server is running"
        }), 200

    # React 메인 페이지
    @app.route("/")
    def frontend_index():
        return send_from_directory(str(FRONTEND_DIR), "index.html")

    # React 정적 파일 및 페이지 제공
    @app.route("/<path:path>")
    def frontend_files(path):
        file_path = FRONTEND_DIR / path

        if file_path.is_file():
            return send_from_directory(str(FRONTEND_DIR), path)

        return send_from_directory(str(FRONTEND_DIR), "index.html")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
