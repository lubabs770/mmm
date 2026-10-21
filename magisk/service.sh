#!/system/bin/sh
# Magisk late_start service: a belt-and-braces start for the RPC service, in case
# BOOT_COMPLETED was missed (a locked device, or a killed app).
until [ "$(getprop sys.boot_completed)" = "1" ]; do
  sleep 2
done
sleep 5

PACKAGE=lubabs770.mmm.modemizer
COMPONENT="$PACKAGE/.service.MmmService"

am start-foreground-service -n "$COMPONENT" >/dev/null 2>&1 ||
  am startservice -n "$COMPONENT" >/dev/null 2>&1

# Doze would otherwise freeze the listener while the screen is off.
dumpsys deviceidle whitelist "+$PACKAGE" >/dev/null 2>&1
