# helot/actions.py
"""Host-side агент: один процесс управляет N эмуляторами/устройствами по ADB.

Роутинг: serial берется из params['serial'] (приоритет) или command['serial'],
иначе используется дефолт из env ANDROID_SERIAL / U2_DEVICE.
"""
import os
import subprocess

# --- 0. Инициализация uiautomator2 + реестр устройств ---
try:
    import uiautomator2 as u2

    _INIT_ERROR = None
except Exception as e:  # модуль должен импортироваться даже без библиотеки
    u2 = None
    _INIT_ERROR = str(e)

_DEFAULT_SERIAL = os.getenv("ANDROID_SERIAL") or os.getenv("U2_DEVICE") or None

# serial -> u2-device (ленивый коннект + кэш). Потокобезопасность не требуется для v1.
_DEVICES: dict = {}

# Legacy-алиас: дефолтное устройство (для обратной совместимости тестов/моков a.d).
d = None


def _default_serial():
    return os.getenv("ANDROID_SERIAL") or os.getenv("U2_DEVICE") or _DEFAULT_SERIAL


def _resolve_serial(params=None, command=None):
    """serial из params (приоритет) -> command -> env-дефолт -> None (дефолт u2)."""
    if isinstance(params, dict) and params.get("serial"):
        return params.get("serial")
    if isinstance(command, dict) and command.get("serial"):
        return command.get("serial")
    return _default_serial()


def get_device(serial=None):
    """Лениво подключает (и кэширует) устройство по serial. serial=None = дефолт."""
    if u2 is None:
        raise ConnectionError(f"uiautomator2 is not available: {_INIT_ERROR}")
    key = serial or _default_serial() or "__default__"
    if key not in _DEVICES:
        target = serial or _default_serial()
        try:
            _DEVICES[key] = u2.connect(target) if target else u2.connect()
        except Exception as e:
            raise ConnectionError(f"Cannot connect to device {target or '<default>'}: {e}")
        # обновляем legacy-алиас на дефолт
        global d
        if key == (_default_serial() or "__default__"):
            d = _DEVICES[key]
    return _DEVICES[key]


def list_devices():
    """Серийники устройств из `adb devices`. Пустой список если adb недоступен."""
    try:
        out = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=10
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []
    serials = []
    for line in out.stdout.strip().splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials

# Белый список полей селектора -> kwargs UiObject.
# description в uiautomator2 соответствует content-desc.
ALLOWED_SELECTOR_KEYS = ("text", "resourceId", "className", "description")

DEFAULT_WAIT_TIMEOUT = 10.0


def _get_device(serial=None):
    """Возвращает устройство по serial (дефолт если None) или None при ошибке."""
    try:
        return get_device(serial)
    except Exception:
        # legacy-фолбэк для тестов, мокающих a.d напрямую
        if serial in (None, _default_serial()) and d is not None:
            return d
        return None


def _require_device(serial=None):
    """Возвращает (device, error_message). error_message is None если устройство готово."""
    try:
        return get_device(serial), None
    except Exception as e:
        # legacy-фолбэк: тесты подменяют a.d напрямую
        if serial in (None, _default_serial()) and d is not None:
            return d, None
        return None, str(e)


def _find_element(selector, serial=None):
    """
    Универсальный селектор: принимает словарь и возвращает UiObject.
    Поддерживает text, resourceId, className, description и их комбинации.
    Пример: {"text": "Settings", "className": "android.widget.TextView"}.
    serial=None = дефолтное устройство.
    """
    if not isinstance(selector, dict) or not selector:
        raise ValueError("selector must be a non-empty dict")

    kwargs = {}
    for key in ALLOWED_SELECTOR_KEYS:
        if key in selector and selector[key] is not None:
            kwargs[key] = selector[key]

    if not kwargs:
        raise ValueError(
            f"selector must contain at least one of {list(ALLOWED_SELECTOR_KEYS)}, got: {selector}"
        )

    device, err = _require_device(serial)
    if err:
        raise ConnectionError(err)

    return device(**kwargs)

# --- 1. Протокол ответов ---

def success_response(message, data=None):
    """Формирует стандартный успешный ответ."""
    response = {
        "status": "success",
        "message": message
    }
    if data is not None:
        response["data"] = data
    return response

def error_response(message):
    """Формирует стандартный ответ об ошибке."""
    return {
        "status": "error",
        "message": message
    }

# --- 2. Обработчики атомарных команд ---

def handle_tap(params):
    selector = params.get('selector')
    serial = params.get('serial')
    if not selector:
        return error_response("Missing required param: 'selector'")
    try:
        element = _find_element(selector, serial=serial)
        element.click()
    except (ValueError, ConnectionError) as e:
        return error_response(str(e))
    except Exception as e:
        return error_response(f"Tap failed for selector {selector}: {e}")
    return success_response(f"Element tapped: {selector}")

def handle_input_text(params):
    selector = params.get('selector')
    text = params.get('text')
    serial = params.get('serial')
    if not selector:
        return error_response("Missing required param: 'selector'")
    if text is None:
        return error_response("Missing required param: 'text'")
    try:
        element = _find_element(selector, serial=serial)
        element.set_text(text)
    except (ValueError, ConnectionError) as e:
        return error_response(str(e))
    except Exception as e:
        return error_response(f"Input text failed for selector {selector}: {e}")
    return success_response("Text input successful.")

def handle_swipe(params):
    sx = params.get('sx', params.get('start_x'))
    sy = params.get('sy', params.get('start_y'))
    ex = params.get('ex', params.get('end_x'))
    ey = params.get('ey', params.get('end_y'))
    duration = params.get('duration', 0.5)
    serial = params.get('serial')
    if sx is None or sy is None or ex is None or ey is None:
        return error_response(
            "Missing required params: 'sx', 'sy', 'ex', 'ey' "
            "(aliases: 'start_x', 'start_y', 'end_x', 'end_y')"
        )
    try:
        sx, sy, ex, ey = int(sx), int(sy), int(ex), int(ey)
        duration = float(duration)
    except (TypeError, ValueError):
        return error_response(
            f"Invalid swipe params: sx={sx!r}, sy={sy!r}, ex={ex!r}, ey={ey!r}, duration={duration!r}"
        )
    device, err = _require_device(serial)
    if err:
        return error_response(err)
    try:
        device.swipe(sx, sy, ex, ey, duration)
    except Exception as e:
        return error_response(f"Swipe failed: {e}")
    return success_response("Swipe completed.")

def handle_back(params):
    serial = params.get('serial') if isinstance(params, dict) else None
    device, err = _require_device(serial)
    if err:
        return error_response(err)
    try:
        device.press("back")
    except Exception as e:
        return error_response(f"Back press failed: {e}")
    return success_response("Back button pressed.")

def handle_wait_for_element(params):
    selector = params.get('selector')
    timeout = params.get('timeout', DEFAULT_WAIT_TIMEOUT)
    serial = params.get('serial')
    if not selector:
        return error_response("Missing required param: 'selector'")
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        return error_response(f"Invalid timeout value: {timeout!r}")
    try:
        element = _find_element(selector, serial=serial)
    except (ValueError, ConnectionError) as e:
        return error_response(str(e))
    try:
        found = element.wait(timeout=timeout)
    except Exception as e:
        return error_response(f"Error waiting for element {selector}: {e}")
    if not found:
        return error_response(f"Element not found within {timeout}s: {selector}")
    return success_response(f"Element found: {selector}")

def handle_get_element_properties(params):
    selector = params.get('selector')
    serial = params.get('serial')
    if not selector:
        return error_response("Missing required param: 'selector'")
    try:
        element = _find_element(selector, serial=serial)
        info = element.info
    except (ValueError, ConnectionError) as e:
        return error_response(str(e))
    except Exception as e:
        return error_response(f"Get properties failed for selector {selector}: {e}")
    return success_response("Properties retrieved.", data=info)


# --- 3. Ядро Диспатчеризации ---

ACTION_HANDLERS = {
    "tap": handle_tap,
    "input_text": handle_input_text,
    "swipe": handle_swipe,
    "back": handle_back,
    "wait_for_element": handle_wait_for_element,
    "get_element_properties": handle_get_element_properties,
}

def dispatch_action(command):
    """
    Принимает команду в виде словаря, вызывает нужный обработчик
    и возвращает результат в стандартном формате.
    serial роутится: params['serial'] (приоритет) или command['serial'].
    """
    action_name = command.get("action")
    params = dict(command.get("params", {}) or {})
    if command.get("serial") and not params.get("serial"):
        params["serial"] = command.get("serial")

    handler = ACTION_HANDLERS.get(action_name)

    if not handler:
        return error_response(f"Unknown action: {action_name}")

    try:
        result = handler(params)
        return result
    except Exception as e:
        return error_response(f"Error executing action '{action_name}': {str(e)}")

# --- Пример использования (для тестов) ---
if __name__ == '__main__':
    print("--- Testing Dispatch Core ---")

    # Тест 1: Успешный тап
    tap_command = {"action": "tap", "params": {"selector": {"text": "Settings"}}}
    print(f"Executing: {tap_command}")
    print(f"Result: {dispatch_action(tap_command)}\n")

    # Тест 2: Успешный ввод текста
    input_command = {"action": "input_text", "params": {"selector": {"resourceId": "user"}, "text": "admin"}}
    print(f"Executing: {input_command}")
    print(f"Result: {dispatch_action(input_command)}\n")

    # Тест 3: Неизвестная команда
    unknown_command = {"action": "delete_everything", "params": {}}
    print(f"Executing: {unknown_command}")
    print(f"Result: {dispatch_action(unknown_command)}\n")
