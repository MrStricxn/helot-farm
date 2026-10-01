# scenarios/registration.py
"""Сценарий регистрации WhatsApp-аккаунта как последовательность шагов helot.

ВНИМАНИЕ: селекторы ниже — отправная точка из ТЗ, а не истина в последней
инстанции. Тексты/resourceId зависят от версии WhatsApp и локали эмулятора.
Перед боем снимите фактическую иерархию (dump) и откалибруйте SELECTORS
либо переопределите их через payload["selectors"] для конкретной задачи.
"""

DEFAULT_SELECTORS = {
    "agree_button": {"text": "Agree and continue"},
    "phone_field": {"resourceId": "com.whatsapp:id/registration_phone"},
    "next_button": {"text": "Next"},
    # экран ввода кода подтверждения — маркер, что номер принят
    "code_screen": {"text": "Verify your number"},
}

DEFAULT_WAIT_TIMEOUT = 60


class RegistrationScenario:
    def __init__(self, runner, payload):
        self.runner = runner
        self.payload = payload or {}
        self.selectors = dict(DEFAULT_SELECTORS)
        self.selectors.update(self.payload.get("selectors") or {})

    def _fail(self, step):
        reason = f"step {step!r} failed"
        if self.runner.last_error:
            reason += f": {self.runner.last_error}"
        return {"status": "failed", "reason": reason}

    def run(self):
        phone = self.payload.get("phone_number")
        if not phone:
            return {"status": "failed", "reason": "payload missing 'phone_number'"}
        wait_timeout = self.payload.get("wait_timeout", DEFAULT_WAIT_TIMEOUT)

        if not self.runner.run_step("tap", {"selector": self.selectors["agree_button"]}):
            return self._fail("agree")
        if not self.runner.run_step(
            "input_text",
            {"selector": self.selectors["phone_field"], "text": phone},
        ):
            return self._fail("input_phone")
        if not self.runner.run_step("tap", {"selector": self.selectors["next_button"]}):
            return self._fail("confirm_next")
        if not self.runner.run_step(
            "wait_for_element",
            {"selector": self.selectors["code_screen"], "timeout": wait_timeout},
        ):
            return self._fail("wait_code_screen")
        return {"status": "success"}
