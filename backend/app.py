from flask import Flask

# Flask 앱 생성
app = Flask(__name__)

# 기본 라우트
@app.route("/")
def home():
    return "Flask is running!"

if __name__ == "__main__":
    # 로컬 개발 서버 실행
    app.run(host="127.0.0.1", port=5000, debug=True)
