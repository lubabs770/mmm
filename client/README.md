# mmm — Python client

```python
from mmm import Android

a = Android()                       # reads ~/.config/mmm/config.toml
print(a.cls("android.os.Build").MODEL)

tm = a.context.getSystemService("phone")
print(tm.getSignalStrength().getLevel())

@a.on("android.provider.Telephony.SMS_RECEIVED", durable="sms")
def got(intent):
    print(intent["extras"])

a.run()
```

Curated helpers, shared with the web UI and the MCP server:

```python
from mmm.android import sms, network
sms.send(a, "+15550100199", "hello")
print(network.wifi(a)["ssid"])
```

Install:

```sh
pip install "git+https://github.com/lubabs770/mmm#subdirectory=client"
```

Pair it with a token from the app's Clients screen:

```sh
mmm pair <token>                 # in Termux, on the phone
mmm pair <token> --ssh phone     # from a computer, through Termux's sshd
mmm pair <token> --adb           # from a computer, over USB
mmm pair <token> --tailnet phone # from a computer on your tailnet
```

`mmm ui` opens the web UI; `mmm mcp` runs an MCP server.

Full documentation: see the `docs/` directory at the repository root.
