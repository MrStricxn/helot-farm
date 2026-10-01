# helot/server.py
"""Host-side командный сервер: принимает JSON-команды и роутит их по устройствам."""
import os

from flask import Flask, jsonify, request

try:
    from helot.actions import dispatch_action, list_devices, success_response
except ImportError:  # запуск как `python helot/server.py`
    from actions import dispatch_action, list_devices, success_response


app = Flask(__name__)


@app.post("/execute")
def execute():
    command = request.get_json(silent=True)
    if command is None:
        return jsonify({"status": "error", "message": "Invalid or missing JSON body"}), 400
    if not isinstance(command, dict):
        return jsonify({"status": "error", "message": "Command must be a JSON object"}), 400

    result = dispatch_action(command)
    status_code = 200 if result.get("status") == "success" else 400
    return jsonify(result), status_code


@app.get("/devices")
def devices():
    serials = list_devices()
    return jsonify(success_response("Devices listed.", data={"devices": serials})), 200


@app.get("/health")
def health():
    return jsonify(success_response("OK")), 200


def run_server(host="0.0.0.0", port=5000):
    """Запуск Flask-приложения командного сервера."""
    host = os.getenv("HOST", host)
    port = int(os.getenv("PORT", port))
    app.run(host=host, port=port)


if __name__ == "__main__":
    run_server()
