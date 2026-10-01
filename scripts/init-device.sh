#!/usr/bin/env bash
# Инициализация ATX-агента (uiautomator2) на всех подключенных устройствах.
# Использование: bash scripts/init-device.sh [serial...]
# Если серийники не переданы — init на всех устройствах из `adb devices`.
set -euo pipefail

adb start-server >/dev/null
echo "== adb devices =="
adb devices

if [ "$#" -gt 0 ]; then
  serials="$*"
else
  serials="$(adb devices | awk '$2=="device" {print $1}')"
fi

if [ -z "${serials}" ]; then
  echo "No devices found" >&2
  exit 1
fi

for s in ${serials}; do
  echo "== init ${s} =="
  python -m uiautomator2 init --serial "${s}"
done
echo OK
