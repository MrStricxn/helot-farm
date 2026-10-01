# helot/stream.py
"""C2-стриминг: WebSocket-сервер интерактивного экрана устройств.

  ws://<helot_ip>:8080/stream/{serial}

Видео: ручной режим scrcpy-server (без GUI-клиента scrcpy) — jar пушится
на устройство, запускается через app_process с raw_stream, сырой H.264
читается с TCP (adb forward) и качается в сокет как binary-фреймы.
NB: стоковый `scrcpy --output=-` пригодный сырой H.264 в stdout НЕ дает
(апстрим-issues Genymobile #6545/#6212), поэтому качаем с сокета сервера.

Протокол (текстовые фреймы — JSON):
  -> {"type": "hello", "serial, "width", "height"}   # первым делом
  -> {"type": "error", "message": ...}               # фатально, затем close
  <- {"type": "click", "x": 100, "y": 250}           # абсолютные px...
  <- {"type": "click", "x": 0.5, "y": 0.3, "relative": true}  # ...или доли
  <- {"type": "swipe", "x1":..,"y1":..,"x2":..,"y2":..,"duration":0.4}

Запуск:  python -m helot.stream   (env: HOST, STREAM_PORT, SCRCPY_SERVER_JAR, ...)
Зависимости хоста: adb в PATH + scrcpy-server.jar версии SCRCPY_VERSION.
"""
import asyncio
import json
import logging
import os
import random
import socket

log = logging.getLogger("helot-stream")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("STREAM_PORT", "8080"))

SCRCPY_JAR = os.getenv("SCRCPY_SERVER_JAR")  # путь к scrcpy-server.jar на хосте
SCRCPY_VERSION = os.getenv("SCRCPY_VERSION", "2.1")
SCRCPY_MAX_SIZE = os.getenv("SCRCPY_MAX_SIZE", "1024")
SCRCPY_BITRATE = os.getenv("SCRCPY_BIT_RATE", "2000000")
# Доп. аргументы сервера key=value через пробел (версии различаются флагами!).
SCRCPY_EXTRA_ARGS = os.getenv("SCRCPY_EXTRA_ARGS", "log_level=info audio=false control=false")

REMOTE_TMP_JAR = "/data/local/tmp/scrcpy-server.jar"
CHUNK = 65536
CONNECT_RETRIES = 25  # ~5 c на старт сервера устройства


def _get_input_device(serial):
    """Хук для тестов: резолв устройства ввода."""
    try:
        from helot.actions import get_device
    except ImportError:
        from actions import get_device
    return get_device(serial)


def _display_size(device):
    info = device.info
    return int(info["displayWidth"]), int(info["displayHeight"])


# --- Построение adb/scrcpy команд (чистые функции — покрыты тестами) ---


def parse_path(path):
    """'/stream/<serial>' -> serial, иначе None."""
    if not path:
        return None
    parts = path.strip("/").split("/")
    if len(parts) == 2 and parts[0] == "stream" and parts[1]:
        return parts[1]
    return None


def build_push_cmd(serial, jar):
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    return cmd + ["push", jar, REMOTE_TMP_JAR]


def build_forward_cmd(serial, local_port, scid):
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    return cmd + ["forward", f"tcp:{local_port}", f"localabstract:scrcpy_{scid:08x}"]


def build_remove_forward_cmd(serial, local_port):
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    return cmd + ["forward", "--remove", f"tcp:{local_port}"]


def build_server_cmd(serial, scid):
    """Команда `adb shell ... app_process ...` запуска scrcpy-server."""
    args = [SCRCPY_VERSION, f"scid={scid:08x}", f"max_size={SCRCPY_MAX_SIZE}",
            f"video_bit_rate={SCRCPY_BITRATE}", "raw_stream=true"]
    args += SCRCPY_EXTRA_ARGS.split()
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    return cmd + ["shell",
                  f"CLASSPATH={REMOTE_TMP_JAR}",
                  "app_process", "/", "com.genymobile.scrcpy.Server"] + args


def find_free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- Ввод: сообщения WS -> устройство ---


def resolve_coords(msg, width, height):
    """Абсолютные px по спеке; при relative:true — доли экрана."""
    if msg.get("relative"):
        return int(float(msg["x"]) * width), int(float(msg["y"]) * height)
    return int(msg["x"]), int(msg["y"])


async def handle_control_message(msg, device, width, height):
    """Выполняет команду управления. Возвращает (ok, reply_dict|None)."""
    mtype = msg.get("type")
    if mtype == "click":
        x, y = resolve_coords(msg, width, height)
        await asyncio.to_thread(device.click, x, y)
        return True, None
    if mtype == "swipe":
        if msg.get("relative"):
            x1, y1 = resolve_coords({"x": msg["x1"], "y": msg["y1"], "relative": True}, width, height)
            x2, y2 = resolve_coords({"x": msg["x2"], "y": msg["y2"], "relative": True}, width, height)
        else:
            x1, y1, x2, y2 = int(msg["x1"]), int(msg["y1"]), int(msg["x2"]), int(msg["y2"])
        duration = float(msg.get("duration", 0.4))
        await asyncio.to_thread(device.swipe, x1, y1, x2, y2, duration)
        return True, None
    return False, {"type": "error", "message": f"unknown command type: {mtype!r}"}


# --- Видео: сокет scrcpy-server -> WebSocket ---


async def _run(cmd):
    """Запуск процесса (stdout/stderr в трубы для диагностики)."""
    return await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )


async def launch_video(serial):
    """Пуш jar -> forward -> старт сервера -> TCP-reader сырого H.264.

    Возвращает (reader, cleanup_fn). Бросает RuntimeError с диагнозом.
    """
    if not SCRCPY_JAR or not os.path.isfile(SCRCPY_JAR):
        raise RuntimeError(
            "scrcpy-server.jar not found (env SCRCPY_SERVER_JAR). "
            "Скачайте jar из релиза scrcpy и укажите путь."
        )
    scid = random.getrandbits(31)
    port = find_free_port()

    async def adb(*args):
        proc = await _run(["adb"] + list(args))
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"adb {' '.join(args)} failed: {err.decode().strip()[:300]}")

    await adb(*build_push_cmd(serial, SCRCPY_JAR)[1:])  # без ведущего 'adb'
    server_proc = await _run(build_server_cmd(serial, scid))
    await asyncio.sleep(0.4)
    fwd_proc = await _run(build_forward_cmd(serial, port, scid))
    await fwd_proc.wait()

    reader = None
    last_err = "no attempt"
    for _ in range(CONNECT_RETRIES):
        try:
            reader, _writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=1.0
            )
            break
        except Exception as e:
            reader = None
            last_err = str(e)
            await asyncio.sleep(0.2)
    if reader is None:
        server_proc.terminate()
        raise RuntimeError(f"scrcpy-server not reachable on tcp:{port}: {last_err}")

    async def cleanup():
        for p in (server_proc,):
            try:
                p.terminate()
            except Exception:
                pass
        try:
            proc = await _run(build_remove_forward_cmd(serial, port))
            await proc.wait()
        except Exception:
            pass

    return reader, cleanup


async def pump_stream(reader, send_bytes):
    """Читает сырой H.264 и шлет binary-фреймы. Возвращает False при EOF."""
    while True:
        chunk = await reader.read(CHUNK)
        if not chunk:
            return False
        await send_bytes(chunk)


# --- WebSocket-сессия ---


async def _send_json(ws, obj):
    await ws.send(json.dumps(obj))


async def stream_session(ws, serial, video_launcher=None, device_resolver=None):
    launcher = video_launcher or launch_video
    resolver = device_resolver or _get_input_device
    cleanup = None
    try:
        try:
            device = resolver(serial)
            width, height = _display_size(device)
        except Exception as e:
            await _send_json(ws, {"type": "error", "message": f"device {serial}: {e}"})
            return
        try:
            reader, cleanup = await launcher(serial)
        except Exception as e:
            await _send_json(ws, {"type": "error", "message": str(e)})
            return
        await _send_json(ws, {"type": "hello", "serial": serial, "width": width, "height": height})

        async def sender():
            ok = await pump_stream(reader, ws.send)
            if not ok:
                await ws.close()

        async def receiver():
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue  # бинарь от клиента игнорируем
                try:
                    msg = json.loads(raw)
                except Exception:
                    await _send_json(ws, {"type": "error", "message": "bad JSON"})
                    continue
                try:
                    ok, reply = await handle_control_message(msg, device, width, height)
                except Exception as e:
                    await _send_json(ws, {"type": "error", "message": f"input failed: {e}"})
                    continue
                if not ok and reply:
                    await _send_json(ws, reply)

        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if cleanup is not None:
            try:
                await cleanup()
            except Exception:
                pass


def _path_of(ws, path_arg):
    if path_arg is not None:
        return path_arg
    req = getattr(ws, "request", None)
    return getattr(req, "path", "") if req is not None else ""


def make_handler(video_launcher=None, device_resolver=None):
    async def handler(ws, *args):
        path = _path_of(ws, args[0] if args else None)
        serial = parse_path(path)
        if not serial:
            await _send_json(ws, {"type": "error", "message": f"bad path: {path!r}, want /stream/{{serial}}"})
            await ws.close()
            return
        await stream_session(ws, serial, video_launcher, device_resolver)

    return handler


def run_stream_server(host=HOST, port=PORT, **kw):
    """Блокирующий запуск WS-сервера (совместим со старым и новым API websockets)."""
    try:
        from websockets.asyncio.server import serve
        modern = True
    except ImportError:
        from websockets import serve
        modern = False

    async def _main():
        handler = make_handler(**kw)
        if modern:
            async with serve(handler, host, port):
                await asyncio.get_running_loop().create_future()
        else:
            await serve(handler, host, port)
            await asyncio.get_running_loop().create_future()

    logging.basicConfig(level=logging.INFO)
    log.info("stream server on ws://%s:%s/stream/{serial}", host, port)
    asyncio.run(_main())


if __name__ == "__main__":
    run_stream_server()
