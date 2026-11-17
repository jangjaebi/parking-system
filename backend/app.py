from flask import Flask, jsonify
from flask_cors import CORS
from database import init_db
from parking_routes import parking_bp

def create_app():
    app = Flask(__name__)
    
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

    @app.route('/api/health', methods=['GET'])
    def health_check():
        return jsonify({
            "status": "success",
            "message": "Smart Parking System API Server is running"
        }), 200

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)