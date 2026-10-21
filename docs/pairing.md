# Pairing

A connection is only accepted after it presents a token, and tokens are created by
pairing in the app.

## Create a token

**Clients → Pair new client**:

1. Give it a label (`laptop`, `termux`, `claude`). It appears in the Activity log.
2. Pick a scope:
    - **Full** — all of Android. What you want for your own code.
    - **Assistant** — SMS, contacts, status and notify only. See
      [token scopes](#token-scopes).
3. Copy the token. Only its SHA-256 is kept on the phone, so it cannot be shown
   again; pair a new client if you lose it.

The dialog also offers the ready-made commands for each transport.

## Transports

MMM listens on `127.0.0.1` and nothing else. How you reach that from elsewhere is up
to the transport.

### `local` — on the phone

In Termux, the port is simply there:

```sh
mmm pair <token>
```

### `ssh` — through a tunnel

The usual setup is Termux's sshd on port 8022:

```sh
mmm pair <token> --ssh phone --ssh-port 8022
```

The client runs `ssh -N -L <local_port>:127.0.0.1:7357 …` itself, waits for the
forward to come up, and starts it again if it dies. `ExitOnForwardFailure` and
keepalives are set, so a dropped Wi-Fi link ends the tunnel instead of hanging it.

`phone` here is whatever your `~/.ssh/config` or hosts file calls it — key
authentication is assumed.

### `adb` — over USB

```sh
mmm pair <token> --adb
mmm pair <token> --adb --serial ZY227xxxxx    # with several devices attached
```

The client runs `adb forward tcp:<local_port> tcp:7357` when it connects.

## The config file

`mmm pair` writes `~/.config/mmm/config.toml`, mode `600`:

```toml
default = "phone"

[profiles.termux]
transport = "local"
token = "…"
host = "127.0.0.1"
port = 7357

[profiles.phone]
transport = "ssh"
token = "…"
host = "127.0.0.1"
port = 7357
local_port = 7400
ssh_host = "phone"
ssh_port = 8022
```

- `port` is where MMM listens **on the phone**; `local_port` is this end of the
  tunnel. They can differ, which is handy with several phones.
- Pick a profile with `mmm --profile phone …`, the `MMM_PROFILE` environment
  variable, or `Android("phone")`.
- `MMM_CONFIG` overrides the path to the file.

Nothing stops you from skipping the file:

```python
Android(token="…", host="127.0.0.1", port=7357)
```

## Token scopes

Scopes are enforced **in the app**, not in the client, so a scoped token stays scoped
no matter what connects with it.

| | Full | Assistant (scoped) |
|---|---|---|
| Any class and method | yes | no |
| `Facade` helpers (SMS, call log, contacts, status, notify) | yes | yes |
| `android.os.Build` | yes | yes |
| Subscribe to any broadcast | yes | SMS, phone state, battery |
| `proxy` (Java listeners) | yes | no |

A scoped token's allow-list is a set of `class#method` patterns where `*` matches any
run of characters. The assistant preset is:

```
lubabs770.mmm.modemizer.rpc.Facade#*
android.os.Build#*
```

Rejected calls raise `SecurityException` on the client and appear in the Activity
screen, so a token that is being used beyond its scope is visible on the phone.

## Revoking

**Clients → Revoke** drops the token, and with it any durable subscriptions it owned.
Open connections using it keep their current session — close them from the Status
screen by stopping the service if that matters.

## Checking it works

```sh
mmm status
```

```json
{
  "clients": 1,
  "model": "Pixel 4a",
  "port": 7357,
  "priv_app": false,
  "protocol": 1,
  "root": true,
  "server": "mmm",
  "version": "0.1.0"
}
```
