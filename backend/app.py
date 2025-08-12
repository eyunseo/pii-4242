from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

@app.get("/")
def home():
    return "Flask is running!"

@app.post("/api/echo")
def echo():
    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    print(f"[ECHO] {text}")
    return jsonify({"ok": True, "received": text})
