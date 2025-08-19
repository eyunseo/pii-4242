from flask import Flask
from flask_cors import CORS
from pii_guard.api import api_bp
from report.view import report_bp
from dotenv import load_dotenv 
import os
import google.generativeai as genai

load_dotenv()  # .env 불러오기
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

app = Flask(__name__)
CORS(app, resources={
    r"/api/*":    {"origins": ["*"], "methods": ["POST", "OPTIONS"], "allow_headers": ["Content-Type"]},
    r"/report/*": {"origins": ["*"], "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type"]},
})

@app.get("/")
def home():
    return "Flask is running!"

app.register_blueprint(api_bp, url_prefix="/api")
app.register_blueprint(report_bp, url_prefix="/report")

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)