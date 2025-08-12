from flask import Flask
from flask_cors import CORS
from pii_guard.api import api_bp

app = Flask(__name__)
CORS(
    app,
    resources={
        r"/api/*": {
            "origins": ["https://chat.openai.com", "https://chatgpt.com"],
            "methods": ["POST", "OPTIONS"],
            "allow_headers": ["Content-Type"],
            "max_age": 600,
        }
    },
)

@app.get("/")
def home():
    return "Flask is running!"

app.register_blueprint(api_bp, url_prefix="/api")

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)