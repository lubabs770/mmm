# Recipes

Short, working programs. All of them assume a paired full-scope token and the
service running.

## An SMS bot

Answers every text that arrives, and keeps the ones that arrive while it is
restarting:

```python
from mmm import Android

a = Android()
facade = a.cls("lubabs770.mmm.modemizer.rpc.Facade")

def reply(to: str, body: str) -> None:
    a.call(facade, "smsSend", a.context, to, body)

@a.on("android.provider.Telephony.SMS_RECEIVED", durable="sms")
def on_sms(intent):
    # The raw PDUs are in the extras; the Facade's message list is easier.
    latest = a.call(facade, "smsList", a.context, 1)
    if not latest:
        return
    message = latest[0]
    sender, text = message["address"], (message["body"] or "").strip()
    print(f"{sender}: {text}")
    if text.lower() == "ping":
        reply(sender, "pong")

a.run()
```

Nothing else was running when a text arrived? The subscription is durable, so the
event is waiting in the backlog and replays as soon as this script connects.

Parsing the PDUs yourself, if you would rather not re-query:

```python
@a.on("android.provider.Telephony.SMS_RECEIVED", durable="sms")
def on_sms(intent):
    pdus = intent["extras"]["pdus"]        # already decoded to bytes
    fmt = intent["extras"]["format"]
    sms = a.cls("android.telephony.SmsMessage")
    for pdu in pdus:
        message = a.call(sms, "createFromPdu", pdu, fmt)
        print(message.getOriginatingAddress(), message.getMessageBody())
```

## A call screener

Watches call state, and with a priv-app install can act on it:

```python
from mmm import Android

a = Android()
facade = a.cls("lubabs770.mmm.modemizer.rpc.Facade")

@a.on("android.intent.action.PHONE_STATE", durable="calls")
def on_call(intent):
    extras = intent["extras"]
    # "RINGING", "OFFHOOK" or "IDLE"; the number needs READ_CALL_LOG.
    if extras.get("state") != "RINGING":
        return
    number = extras.get("incoming_number") or ""
    print("ringing:", number)
    known = a.call(facade, "contactsSearch", a.context, number, 1)
    if not known:
        print("not in contacts")
        # Needs the Magisk module (MODIFY_PHONE_STATE):
        # telecom = a.context.getSystemService("telecom")
        # a.call(telecom, "endCall", main=True)

a.run()
```

Call state arrives as a broadcast, so this is a plain subscription. (The older
`PhoneStateListener` is a class, not an interface, so it can't be a proxy; see
[Java listeners](python.md#java-listeners-proxies).) Handlers run on their own
thread, which is why calling the phone from inside one is fine.

## A notification forwarder

Broadcasts the phone's own state somewhere else — here, printing a line whenever the
battery or the network changes:

```python
import json
from mmm import Android

a = Android()

a.subscribe(
    [
        "android.intent.action.BATTERY_CHANGED",
        "android.net.conn.CONNECTIVITY_CHANGE",
    ],
    name="device",
    durable=True,
)

for event in a.events():
    extras = event.payload.get("extras", {})
    print(json.dumps({"action": event.payload["action"], "extras": extras}))
```

Piping it into something else is the same thing from the shell:

```sh
mmm watch android.intent.action.BATTERY_CHANGED --durable battery | jq -c '.payload.extras.level'
```

## Posting a notification on the phone

```python
a.call(a.cls("lubabs770.mmm.modemizer.rpc.Facade"), "notify", a.context,
       "Build finished", "deploy #42 is live")
```

## Device status in one call

```python
status = a.call(a.cls("lubabs770.mmm.modemizer.rpc.Facade"), "deviceStatus", a.context)
print(status["signalLevel"], status["batteryPercent"], status["networkOperator"])
```

Or the long way round, which is what the facade does:

```python
tm = a.context.getSystemService("phone")
print(tm.getSignalStrength().getLevel())
print(tm.getNetworkOperatorName())
```

## Answering a call (priv-app only)

```python
telecom = a.context.getSystemService("telecom")
a.call(telecom, "acceptRingingCall", main=True)
a.call(telecom, "endCall", main=True)
```

Both need `MODIFY_PHONE_STATE`, which only a priv-app install can hold. Without the
Magisk module these raise `SecurityException`.

## One-off calls from the shell

```sh
mmm call android.os.Build MODEL
mmm call android.os.Build getRadioVersion
mmm call android.os.SystemClock elapsedRealtime
mmm status
```

Arguments are parsed as JSON when they parse, and as strings otherwise:

The CLI has one limit worth knowing: `Facade` methods want a `Context`, and there is
no way to write one on a command line. Use `mmm repl` for those:

```sh
mmm repl
>>> a.call(a.cls("lubabs770.mmm.modemizer.rpc.Facade"), "smsList", a.context, 5)
```
