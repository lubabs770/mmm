# Python guide

The client is pure Python with no dependencies, so it installs the same way on
Termux, Linux, macOS and Windows.

```sh
pip install "git+https://github.com/lubabs770/mmm#subdirectory=client"
```

## Connecting

```python
from mmm import Android

a = Android()                 # the default profile from ~/.config/mmm/config.toml
a = Android("phone")          # a named profile
a = Android(token="…", port=7357)   # no config file at all
```

`Android` connects straight away. Use it as a context manager, or call `close()`
when you are done:

```python
with Android() as a:
    print(a.ping())
```

If the link drops it reconnects on its own, with backoff, and re-subscribes
everything — pass `reconnect=False` to turn that off. **Handles do not survive a
reconnect**: they belong to a session. Fetch objects again after one.

## Objects and calls

Three ways in:

```python
a.context                         # the app's Context
a.cls("android.os.Build")         # a class, for statics and constants
a.new("android.content.Intent", "android.intent.action.VIEW")
```

Then attribute access does what it looks like:

```python
tm = a.context.getSystemService("phone")     # a method call
level = tm.getSignalStrength().getLevel()    # chained calls
model = a.cls("android.os.Build").MODEL      # a field read
```

A name on an object can be either a method or a field, and only Java knows which.
Calling it makes it a method; using it as a value reads a field. When you want to be
explicit:

```python
build = a.cls("android.os.Build")
build.get("MODEL")                 # field
build.call("getRadioVersion")      # method
a.call(build, "getRadioVersion")   # the same, spelled out
```

### Choosing an overload

JSON carries no type widths, so `5` could be an `int` or a `long`. The engine scores
every candidate against the arguments that actually arrived and picks the closest; an
`int` parameter wins for a Python `int`, `double` for a `float`, `String` over
`CharSequence` over `Object`.

When two candidates are equally close it refuses rather than guess, and tells you
what it found:

```python
a.call(obj, "setValue", 1, sig=["long"])     # force one
```

### Arguments and results

| Python | Java |
|---|---|
| `None`, `bool`, `int`, `float`, `str` | the matching primitive or `String` |
| `bytes` | `byte[]` (base64 on the wire) |
| `list`, `tuple` | `List`, or a typed array when the parameter wants one |
| `dict` | `Map` |
| `JavaObject` | the object itself |

Results come back the same way. Anything that isn't a plain value becomes a
`JavaObject` — a handle to the real object on the phone, which you keep using.

### Snapshots

A handle is no use in a log file. `expand()` flattens a Bundle, an Intent or a plain
object into JSON:

```python
intent = a.new("android.content.Intent", "android.intent.action.VIEW")
print(intent.expand())
# {'action': 'android.intent.action.VIEW', 'extras': {}, 'flags': 0, …}

a.call(obj, "getSomething", expand=True)     # expand the result of one call
```

### The main thread

Some APIs insist on the main Looper — most UI, and some telephony listeners:

```python
a.call(obj, "startListening", listener, main=True)
```

Everything else runs on a worker pool, so several calls can be in flight at once.

## Events

### Broadcasts

```python
@a.on("android.provider.Telephony.SMS_RECEIVED", durable="sms")
def got(intent):
    print(intent["extras"]["format"], intent["action"])

a.run()        # blocks, dispatching events, until Ctrl-C
```

or without the decorator:

```python
name = a.subscribe(
    ["android.intent.action.BATTERY_CHANGED"],
    name="battery",
    durable=True,
    handler=print,
)
a.unsubscribe(name)
```

A broadcast that carries a MIME type only matches a subscription that names it, so
pass `types` for those (app 0.2.3 or later). An MMS waiting to be fetched is one:

```python
a.subscribe(["android.provider.Telephony.WAP_PUSH_RECEIVED"], name="mms",
            durable=True, types=["application/vnd.wap.mms-message"], handler=print)
```

A **durable** subscription keeps receiving while nobody is connected. Its events wait
in a ring buffer (500 events or 24 hours by default) and replay when a client
subscribes again under the same name with the same token — so an SMS that arrives
while your script is restarting still reaches it. Durable subscriptions are also
re-registered when the phone reboots.

Broadcast payloads are always plain JSON: action, data, type, package, component,
flags, categories and expanded extras. Anything in the extras that cannot be
represented becomes `{"$opaque": "<class>", "repr": "…"}` rather than a handle,
because a buffered event may be replayed into a different session.

### Iterating instead of callbacks

```python
for event in a.events():
    print(event.sub, event.event_id, event.payload)
```

Good for pipelines; `mmm watch` is this with `json.dumps` around it.

### Java listeners (proxies)

`proxy()` makes a real Java object implementing the interfaces you name, whose
method calls arrive as events:

```python
sensors = a.context.getSystemService("sensor")
light = sensors.getDefaultSensor(5)          # Sensor.TYPE_LIGHT

def listener(method, args):
    if method == "onSensorChanged":
        print("lux:", args[0]["values"][0])
    return None

on_light = a.proxy("android.hardware.SensorEventListener", listener)
a.call(sensors, "registerListener", on_light, light, 3, main=True)   # SENSOR_DELAY_NORMAL
```

Only **interfaces** can be proxied — that is all `java.lang.reflect.Proxy` can do.
Plenty of Android listeners are abstract classes instead (`PhoneStateListener`,
`BroadcastReceiver`); for broadcasts, use `subscribe`.

The handler is called as `handler(method_name, args)` on the client's event thread
(one thread, so handlers run one at a time, in order), and may call the phone itself. A `void`
method returns at once; anything else waits for your return value, with a timeout
(2 s by default) after which Java gets a harmless default — an Android callback that
never returns can take the system service down with it.

Proxies need a full-scope token, and their events are never buffered.

## Curated helpers: `mmm.android`

Plain functions over a connection, for the jobs everyone needs. The web UI, the MCP
server and `mmm serve` use the same ones.

| Module | Functions | Needs |
|---|---|---|
| `mmm.android` | `facade(a, method, *args)`, `shell(a, command)`, `restart_listener(a)` | `shell`: root |
| `mmm.android.sms` | `send`, `recent`, `decode`, `send_mms`, `download_mms`, `parse_notification`, `parse_retrieved` | MMS: full token; receiving also root |
| `mmm.android.calls` | `log`, `contacts` | scoped token is enough |
| `mmm.android.notifications` | `active` (every app's), `post` | `active`: root |
| `mmm.android.apps` | `installed`, `set_enabled` | root |
| `mmm.android.network` | `wifi` (state, signal, IP, saved networks) | root |
| `mmm.android.settings` | `read`, `write` (system / secure / global) | full token |

Anything that goes through the app's `Facade` works with a scoped token; the rest is
raw reflection, or root through the app's own `su` helper (`shell`).

```python
from mmm import Android
from mmm.android import calls, notifications, sms

with Android() as a:
    for n in notifications.active(a):
        print(n["package"], n["title"], n["text"])
    print(calls.log(a, 5))
    sms.send_mms(a, "+15550100199", open("chart.png", "rb").read(), "chart.png", "image/png", "today")
```

Sending an MMS builds the message on the computer, writes it to a MediaStore row the
app owns and hands it to the platform's MMS service, so it needs no change to the
app. Receiving goes the other way: `parse_notification` reads the push that says a
message is waiting, `download_mms` fetches it through the same platform service, and
`parse_retrieved` returns its parts (`type`, `name`, `data`). `mmm serve` does all of
this for you and hands the pictures to your [rules](ui.md).

## Settings

Every setting the UI can change is also settable over RPC:

```python
a.settings()
a.set_setting("port", 7400)      # takes effect when the listener restarts
```

## Errors

```python
from mmm import AndroidError, MmmError, NotConnected

try:
    a.call(a.cls("android.os.Build"), "nope")
except AndroidError as error:
    print(error.type)        # java.lang.NoSuchMethodException, SecurityException, …
    print(error.message)
    print(error.java_stack)  # the stack from the phone
```

- `AndroidError` — something failed on the phone.
- `NotConnected` — the link is down.
- `TimeoutError` — no reply within `timeout` (30 s by default).

## Housekeeping

Handles are released when the Python object is collected; the client batches the
`release` calls. `JavaObject.release()` frees one immediately. The handle table is
capped (50 000 by default) so a leaking loop fails loudly instead of eating the
phone's memory.
