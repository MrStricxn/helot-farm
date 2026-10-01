# scenarios/client.py
"""Тонкий HTTP-клиент helot-агента, привязанный к одному устройству."""
import os

import requests


class HelotClient:
    """POST /execute на helot. 400 от агента — валидный ответ исполнения."""

    def __init__(self, serial, base_url=None, timeout=10):
        self.serial = serial
        self.base_url = (base_url or os.getenv("HELOT_API_URL", "http://127.0.0.1:5000")).rstrip("/")
        self.timeout = timeout

    def execute(self, action, params):
        """Возвращает envelope агента. Транспортные ошибки — исключением."""
        body = {"serial": self.serial, "action": action, "params": params or {}}
        r = requests.post(f"{self.base_url}/execute", json=body, timeout=self.timeout)
        if r.status_code not in (200, 400):
            r.raise_for_status()
        return r.json()
