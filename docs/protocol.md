# Protocol

Newline-delimited JSON over a TCP socket on `127.0.0.1:7357`. One JSON object per
line, in both directions, so you can drive it with `nc` while debugging.

```sh
# on the phone, in Termux
printf '{"op":"auth","id":1,"token":"<token>"}\n{"op":"call","id":2,"target":"android.os.Build","method":"toString"}\n' \
  | nc 127.0.0.1 7357
```

Every request carries an `id`; replies echo it. Several requests can be in flight at
once, and replies may arrive out of order.

## Handshake

The **first** message must be `auth`. Anything else closes the connection, and the
attempt is recorded in the Activity log.

```json
{"op":"auth","id":1,"token":"a1b2c3…"}
```

```json
{"op":"result","id":1,"value":{
  "server":"mmm","version":"0.1.0","protocol":1,"port":7357,
  "model":"Pixel 4a","android_release":"13","sdk_int":33,
  "root":true,"priv_app":false,"clients":1,
  "handle_cap":50000,"proxy_reply_timeout_ms":2000,
  "client":"laptop","client_id":"4f2a19cd","scope":{"full":true,"methods":[],"actions":[]}
}}
```

A wrong or revoked token gets an `unauthorized` error and the connection closes.

## Requests

| op | fields | returns |
|---|---|---|
| `auth` | `token` | server info (above) |
| `ping` | — | server info |
| `context` | — | handle to the app's `Context` |
| `cls` | `name` | handle to a `Class`, plus `name` |
| `new` | `cls`, `args`, `sig?` | handle to the new object |
| `call` | `target`, `method`, `args`, `sig?`, `main?`, `$expand?` | the return value |
| `get` | `target`, `field` | the field's value |
| `set` | `target`, `field`, `value` | `true` |
| `expand` | `target`, `$expand` | a JSON snapshot of the target |
| `release` | `handles` | how many existed |
| `proxy` | `interfaces` | handle to the proxy, plus `sub` |
| `subscribe` | `name`, `action`, `durable?` | `{subscribed, durable, replayed}` |
| `unsubscribe` | `name` | `true` if it existed |
| `reply` | `event_id`, `value` | `true` |

### Targets

`target` is one of:

- a handle object — `{"$h": 42}`
- a class name — `"android.os.Build"` (a static call)
- a class reference — `{"$cls": "android.os.Build"}`
- a bare handle number — `42`

A `Class` target means a static call; an instance target means an instance call.
`resolveClass` also accepts `int`, `java.lang.String[]` and `int[][]`.

### Overloads

Without `sig`, the engine collects every method of that name whose parameters could
accept the arguments, scores each against the arguments that actually arrived
(exact type beats widening beats `Object`), and takes the closest. Equally close
candidates are an error that lists them:

```json
{"op":"error","id":7,"type":"lubabs770.mmm.modemizer.rpc.Overloads$Ambiguous",
 "message":"ambiguous overload; pass sig to choose: setValue(java.lang.Integer); setValue(java.lang.Long)"}
```

`sig` forces a choice by canonical parameter type names:

```json
{"op":"call","id":8,"target":{"$h":3},"method":"setValue","args":[1],"sig":["long"]}
```

### Threading

Calls run on a worker pool. `"main": true` runs one on the main Looper, for APIs that
require it. `release`, `reply`, `unsubscribe` and `ping` are handled on the reading
thread, so they keep their order relative to each other.

## Values

| JSON | Java |
|---|---|
| `null`, `true`, `12`, `1.5`, `"text"` | the matching primitive or `String` |
| `{"$b64": "AAEC"}` | `byte[]` |
| `[…]` | `List`, or a typed array when the parameter is one |
| `{"k": …}` | `Map` |
| `{"$h": 42, "cls": "…"}` | the object that handle refers to |
| `{"$cls": "java.lang.String"}` | a `Class` |

Returned values follow the same table. Integers arrive as JSON numbers (`long`
width); `float` and `double` as JSON doubles; `char` as a one-character string.
Enums come back as a handle with an extra `"name"`.

**Anything else becomes a handle.** Handles belong to the session that created them
and are freed by `release` or when the connection ends.

### Snapshots

`{"$expand": true}` on a `call` returns a JSON snapshot instead of a handle:

- an `Intent` → `action`, `data`, `type`, `package`, `component`, `flags`,
  `categories`, `extras`
- a `Bundle` → each key, recursively
- anything else → `{"$pojo": "<class>"}` plus its no-argument getters and public
  fields

The `expand` op does the same for an object you already hold a handle to.

Where a value cannot be represented and no handle can be given — inside a buffered
event — it becomes `{"$opaque": "<class>", "repr": "…"}`.

## Replies

```json
{"op":"result","id":12,"value":…}
{"op":"error","id":12,"type":"java.lang.SecurityException","message":"…","java_stack":"…"}
```

`type` is the exception's class name, or one of the protocol's own: `protocol`,
`unauthenticated`, `unauthorized`. `java_stack` is the stack from the phone, trimmed
to 4 kB.

## Events

```json
{"op":"event","sub":"sms","event_id":41,"ts":1731000000000,
 "payload":{"action":"android.provider.Telephony.SMS_RECEIVED","extras":{…}}}
```

### Broadcasts

```json
{"op":"subscribe","id":5,"name":"sms","durable":true,
 "action":["android.provider.Telephony.SMS_RECEIVED"]}
```

A subscription is named by the client and owned by its token, so two clients can use
the same name. Subscribing again with the same name re-attaches it and replays
whatever is in the backlog; `replayed` in the reply says how many events that was.

`durable` subscriptions:

- keep their receiver registered after the connection ends,
- buffer into a ring (500 events or 24 hours by default, both configurable),
- are saved to disk and re-registered when the service starts, including after a
  reboot.

Broadcast payloads are **always plain JSON** — never handles — because a buffered
event may be replayed into a session that did not create it.

### Proxies

```json
{"op":"proxy","id":9,"interfaces":["android.os.Handler$Callback"]}
{"op":"result","id":9,"value":{"$h":77,"cls":"android.os.Handler$Callback","sub":"proxy#1"}}
```

Pass handle 77 wherever Java wants that listener. Its calls arrive as events under
`sub`:

```json
{"op":"event","sub":"proxy#1","event_id":42,"ts":…,"needs_reply":true,
 "payload":{"proxy":"proxy#1","method":"handleMessage","sig":["android.os.Message"],"args":[{"$pojo":"android.os.Message","what":1,…}]}}
```

- `needs_reply` is absent for `void` methods; just handle them.
- When it is set, send `{"op":"reply","event_id":42,"value":…}`. If nothing arrives
  within the proxy reply timeout (2 s by default), Java gets a default value —
  `false`, `0`, `null` — because a blocked callback can take a system service down.
- A reply that doesn't fit the method's return type is treated like no reply.
- Only interfaces can be proxied; naming a class is an error.
- `proxy` needs a full-scope token, and proxy events are never buffered: they carry
  session handles and a reply has nowhere to go once the client is gone.

## Scopes

A scoped token's allow-list is checked on `call`, `new`, `get` and `set` against
`class#member` (`<init>` for `new`), and on `subscribe` against each action. `*`
matches any run of characters. A rejection is a `java.lang.SecurityException` and an
entry in the Activity log.

For a scoped token, an allowed pattern only reaches **public** members, and `expand`
(the op or the `$expand` flag) is refused, because expanding runs every getter of an
object without checking any of them.

A token revoked in the app is cut off at once: its open connections close, and a
request already on its way gets an `unauthorized` error.

## Limits

- One handle table per session, capped (50 000 by default). Handle numbers are unique
  across sessions, so a handle from an earlier connection is simply unknown.
- The listener binds `127.0.0.1` only, with a backlog of 8.
- `auth` must arrive within 10 seconds, in a line of at most 64 KiB; at most 16
  connections may be waiting to authenticate at once.
- After that, a request line may be up to 32 MiB.
