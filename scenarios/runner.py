# scenarios/runner.py
"""Исполнитель шагов сценария поверх HelotClient."""
import logging

log = logging.getLogger("helot-scenario")


class ScenarioRunner:
    """Контракт шага: True = агент вернул success, False = ошибка (см. last_error)."""

    def __init__(self, helot_client, device_serial):
        self.client = helot_client
        self.device_serial = device_serial
        self.last_reply = None
        self.last_error = None

    def run_step(self, action, params):
        try:
            reply = self.client.execute(action, params)
        except Exception as e:
            self.last_reply = None
            self.last_error = f"transport: {e}"
            log.warning("step %s failed on %s: %s", action, self.device_serial, e)
            return False
        self.last_reply = reply
        if reply.get("status") == "success":
            self.last_error = None
            return True
        self.last_error = str(reply.get("message"))
        return False
