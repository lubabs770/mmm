# MCP — letting an assistant use the phone

MCP (Model Context Protocol) is the standard way AI assistants plug into tools.
`mmm mcp` is an MCP server on stdio, built into the Python client, so it runs
wherever the client runs: in Termux on the phone, or on a computer with a tunnel.

```sh
mmm mcp                 # speaks MCP on stdin/stdout
mmm --profile phone mcp
```

## Pair a scoped token first

In the app: **Clients → Pair new client → Assistant**. That token can reach the
curated operations and nothing else:

```
lubabs770.mmm.modemizer.rpc.Facade#*
android.os.Build#*
```

plus three broadcast actions (SMS received, phone state, battery). The limit is
enforced **in the app**, so it holds no matter what the assistant asks for. Rejected
calls appear on the Activity screen.

A full token works too, and unlocks `android_call`.

## Tools

| Tool | What it does |
|---|---|
| `sms_send` | `{to, body}` — sends an SMS, splitting long ones into parts |
| `sms_list` | `{limit}` — recent messages, newest first |
| `call_log` | `{limit}` — recent calls |
| `contacts_search` | `{term, limit}` — contacts matching a name or number |
| `device_status` | signal, battery, operator, SIM state, model |
| `notify` | `{title, text}` — posts a notification on the phone |
| `events_wait` | `{subscription, timeout, actions?}` — waits for new events |
| `android_call` | `{cls, method, args, on_context?}` — anything at all |

`android_call` is only offered to a full-scope token. With a scoped one it is not
listed, and the app would refuse it anyway.

`events_wait` creates a **durable** subscription the first time it is asked for one,
so events that arrive between questions are not lost. `sms`, `calls` and `battery`
are known names; anything else needs an `actions` list.

## Wiring it into Claude Code

```sh
claude mcp add phone -- mmm mcp
```

or, with a profile and the single-file build:

```sh
claude mcp add phone -- python /path/to/mmm.pyz --profile phone mcp
```

## Wiring it into a desktop client

Most MCP clients take a JSON config of the same shape:

```json
{
  "mcpServers": {
    "phone": {
      "command": "mmm",
      "args": ["--profile", "phone", "mcp"]
    }
  }
}
```

## Notes

- The server starts whether or not the phone is reachable; it connects on the first
  tool call, and reconnects after a drop.
- A failing tool comes back as `isError: true` with the Java exception's message
  rather than killing the session.
- `mmm mcp` holds one connection to the phone. Several assistants mean several
  tokens — which is also how the Activity log tells them apart.
