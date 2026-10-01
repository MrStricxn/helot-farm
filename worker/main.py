# worker/main.py
"""Воркер фермы: связывает Панель (задачи) и helot-агента (исполнение).

Цикл: helot GET /devices -> для каждого serial panel GET next_task ->
IN_PROGRESS -> helot POST /execute -> COMPLETED/FAILED.
"""
import logging
import os
import sys
import time

import requests

try:
    from scenarios.client import HelotClient
    from scenarios.registration import RegistrationScenario
    from scenarios.runner import ScenarioRunner
except ImportError:  # запуск как `python worker/main.py`
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from scenarios.client import HelotClient
    from scenarios.registration import RegistrationScenario
    from scenarios.runner import ScenarioRunner

PANEL_API_URL = os.getenv("PANEL_API_URL", "http://127.0.0.1:8000").rstrip("/")
HELOT_API_URL = os.getenv("HELOT_API_URL", "http://127.0.0.1:5000").rstrip("/")
POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", "5.0"))
REQUEST_TIMEOUT = float(os.getenv("WORKER_REQUEST_TIMEOUT", "10"))

log = logging.getLogger("helot-worker")


# --- Трансляция задач панели в команды helot ---
# Формат helot: плоский {"serial", "action", "params"} (serial роутится
# диспетчером из верхнего уровня команды).
# Если payload задачи уже содержит {"action", ...} — сквозной пропуск.
# Иначе нужен именованный транслятор под конкретный task_type.


def _passthrough(payload):
    if isinstance(payload, dict) and isinstance(payload.get("action"), str):
        params = payload.get("params", {})
        return payload["action"], params if isinstance(params, dict) else {}
    return None


TRANSLATORS = {}
SCENARIOS = {}


def register_translator(task_type, fn):
    """Зарегистрировать транслятор высокоуровневой задачи в (action, params)."""
    TRANSLATORS[task_type] = fn


def register_scenario(task_type, handler):
    """Зарегистрировать многошаговый сценарий: handler(task, serial) -> result-dict."""
    SCENARIOS[task_type] = handler


def build_helot_command(task, serial):
    """(action, params) для helot или (None, reason) если транслировать нечем."""
    payload = task.get("payload") or {}
    direct = _passthrough(payload)
    if direct is not None:
        return direct[0], direct[1]
    translator = TRANSLATORS.get(task.get("task_type"))
    if translator is None:
        return None, f"No translator for task_type={task.get('task_type')!r}"
    try:
        return translator(payload)
    except Exception as e:
        return None, f"Translator error: {e}"


# --- HTTP-клиенты (транспортные ошибки -> исключения) ---


def run_register_account(task, serial):
    """Транслятор REGISTER_ACCOUNT: HelotClient -> ScenarioRunner -> RegistrationScenario."""
    client = HelotClient(serial=serial, base_url=HELOT_API_URL, timeout=REQUEST_TIMEOUT)
    runner = ScenarioRunner(client, serial)
    scenario = RegistrationScenario(runner, task.get("payload") or {})
    return scenario.run()


register_scenario("REGISTER_ACCOUNT", run_register_account)


def helot_get_devices():
    r = requests.get(f"{HELOT_API_URL}/devices", timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json().get("data") or {}
    return data.get("devices", [])


def panel_get_next_task(serial):
    r = requests.get(f"{PANEL_API_URL}/devices/{serial}/next_task", timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("task")


def panel_set_task_status(task_id, status, result=None):
    body = {"status": status}
    if result is not None:
        body["result"] = result
    r = requests.patch(f"{PANEL_API_URL}/tasks/{task_id}", json=body, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def helot_execute(serial, action, params):
    """POST /execute. 400 от агента — валидный ответ (ошибка исполнения), не транспортная."""
    body = {"serial": serial, "action": action, "params": params}
    r = requests.post(f"{HELOT_API_URL}/execute", json=body, timeout=REQUEST_TIMEOUT)
    if r.status_code not in (200, 400):
        r.raise_for_status()
    return r.json()


# --- Обработка ---


def process_serial(serial):
    """Одно устройство: взять задачу, исполнить, финализировать. Возвращает итог или None."""
    try:
        task = panel_get_next_task(serial)
    except Exception as e:
        log.warning("next_task failed for %s: %s", serial, e)
        return None
    if not task:
        return None

    task_id = task["id"]
    try:
        panel_set_task_status(task_id, "IN_PROGRESS")
    except Exception as e:
        log.warning("task %s: cannot mark IN_PROGRESS: %s", task_id, e)
        return None

    scenario_handler = SCENARIOS.get(task.get("task_type"))
    if scenario_handler is not None:
        try:
            outcome = scenario_handler(task, serial)
        except Exception as e:
            log.warning("task %s: scenario crashed: %s", task_id, e)
            outcome = {"status": "failed", "reason": f"scenario crashed: {e}"}
        final = "COMPLETED" if outcome.get("status") == "success" else "FAILED"
        result = {"scenario": outcome} if final == "COMPLETED" else {"error": outcome.get("reason"), "scenario": outcome}
        try:
            panel_set_task_status(task_id, final, result=result)
        except Exception as e:
            log.warning("task %s: cannot mark %s: %s", task_id, final, e)
            return {"task_id": task_id, "final": "UNREPORTED", "reason": str(e)}
        entry = {"task_id": task_id, "final": final}
        if final == "FAILED":
            entry["reason"] = outcome.get("reason")
        return entry

    action, params_or_reason = build_helot_command(task, serial)
    if action is None:
        try:
            panel_set_task_status(task_id, "FAILED", result={"error": params_or_reason})
        except Exception as e:
            log.warning("task %s: cannot mark FAILED: %s", task_id, e)
        return {"task_id": task_id, "final": "FAILED", "reason": params_or_reason}

    try:
        agent_reply = helot_execute(serial, action, params_or_reason)
    except Exception as e:
        log.warning("task %s: helot unreachable: %s", task_id, e)
        try:
            panel_set_task_status(task_id, "FAILED", result={"error": f"helot unreachable: {e}"})
        except Exception as e2:
            log.warning("task %s: cannot mark FAILED: %s", task_id, e2)
        return {"task_id": task_id, "final": "FAILED", "reason": f"helot unreachable: {e}"}

    if agent_reply.get("status") == "success":
        final, result = "COMPLETED", {"reply": agent_reply}
    else:
        final, result = "FAILED", {"error": agent_reply.get("message"), "reply": agent_reply}
    try:
        panel_set_task_status(task_id, final, result=result)
    except Exception as e:
        log.warning("task %s: cannot mark %s: %s", task_id, final, e)
        return {"task_id": task_id, "final": "UNREPORTED", "reason": str(e)}
    return {"task_id": task_id, "final": final}


def run_once():
    """Одна итерация по всем устройствам. Сбой одного устройства/задачи остальных не касается."""
    try:
        serials = helot_get_devices()
    except Exception as e:
        log.warning("helot /devices failed: %s", e)
        return {"devices": [], "processed": []}
    processed = []
    for serial in serials:
        try:
            outcome = process_serial(serial)
            if outcome:
                processed.append(outcome)
        except Exception as e:  # последний рубеж: цикл жить обязан
            log.exception("unexpected error on %s: %s", serial, e)
    return {"devices": serials, "processed": processed}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("worker start: panel=%s helot=%s interval=%ss", PANEL_API_URL, HELOT_API_URL, POLL_INTERVAL)
    while True:
        try:
            run_once()
        except Exception as e:  # цикл бессмертен
            log.exception("run_once crashed: %s", e)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
