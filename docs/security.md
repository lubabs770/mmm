# Security model

**MMM gives full control of the phone to anyone holding a valid full-scope token.**
That is the design, not an oversight: the whole point is to reach all of Android.
Everything below follows from taking that seriously.

## What protects the port

- **Loopback by default.** The listener binds `127.0.0.1`. Nothing on the LAN can
  reach it; the Status screen shows exactly what it is bound to.
- **Remote access borrows someone else's authentication.** An SSH tunnel or
  `adb forward` is the way in from another machine, and both authenticate before
  any MMM traffic exists.
- **Tailnet is opt-in.** With **Settings → Listen on tailnet** on, MMM also binds the
  phone's Tailscale address (100.64.0.0/10) and drops any connection whose source
  is outside that range. Tailscale encrypts the link and decides which devices are
  on your tailnet, so it takes the place of SSH. The difference is that **every
  device on your tailnet can reach the port and try a token**, where SSH would have
  asked for a key first. Use Tailscale ACLs to narrow that if your tailnet is shared.
- **A token on top.** The first message on every connection must be `auth`, and a
  wrong token closes the connection and is logged.
- **No free waiting room.** Any app on the phone can open a loopback connection, so
  one that hasn't authenticated gets 10 seconds and a 64 KiB line, and at most 16 may
  wait at once. That keeps an unpaired app from tying the server up.

On a rooted phone, any app running as root can reach a loopback port, and any app
with your token can use it. MMM does not defend against code you have already given
root to.

## Tokens

- 32 random bytes from `SecureRandom`, shown once as hex.
- Only the SHA-256 is stored, in app-private storage, so a copy of the app's data
  cannot be turned back into a working token. Comparison is constant-time.
- The client id shown in the UI is its own random value, revealing nothing about the
  token.
- Revoking one takes effect immediately: its open connections are closed, new ones
  are refused, and its durable subscriptions are dropped.
- The client stores tokens in `~/.config/mmm/config.toml`, mode `600`. Treat that
  file the way you treat an SSH private key.

## Scopes

A token is `full` or `scoped`. A scoped token carries an allow-list of
`class#method` patterns and of broadcast actions, checked **in the app** on every
`call`, `new`, `get`, `set` and `subscribe`. The client cannot widen it; a modified
client gains nothing.

Scoped tokens cannot create proxies, because a Java listener can reach whatever the
listener's own API exposes.

A scoped pattern reaches only **public** members — reflection could otherwise force
open a class's private methods and fields too — and scoped tokens cannot `expand`,
which would run every getter of an object without checking any of them.

The assistant preset points at one class, `Facade`, whose methods are the curated
operations. That is deliberate: a short allow-list is one you can actually read.

## Signing

Release APKs are signed with a key kept in CI secrets and **never committed**. A
leaked signing key would let anyone sign an "update" that replaces a privileged app
holding SMS, contacts and call-control permissions. Debug builds use a different key,
so a debug APK cannot silently upgrade a release install.

That debug key is committed to the repository, so it is public: anyone can sign an
APK that installs over a **debug** build and inherits its permissions and data. Debug
builds are for testing; run a release build on a phone you care about. A tagged
release refuses to publish if the release key is missing.

## Privileged permissions

The Magisk module installs MMM as a priv-app together with a generated allowlist
(`privapp-permissions-mmm.xml`). This is the only way to hold `MODIFY_PHONE_STATE`
and `READ_PRIVILEGED_PHONE_STATE`.

The allowlist is generated from the manifest at build time, because on Android 9 and
later with `ro.control_privapp_permissions=enforce` a priv-app requesting a
privileged permission it is not allowlisted for **can stop the phone from booting**.
A hand-written file that falls behind the manifest is a bricked boot; a generated one
cannot fall behind.

## Device Owner

MMM ships a `DeviceAdminReceiver` so it can be made Device Owner
(`adb shell dpm set-device-owner lubabs770.mmm.modemizer/.AdminReceiver`). Doing so
hands every **full-scope** token the whole `DevicePolicyManager` API, including
`wipeData`, `lockNow` and silent app installs. Only pair full-scope tokens you
would trust with a factory reset; scoped tokens cannot reach `DevicePolicyManager`.

## What is recorded

The Activity screen keeps a rolling log on the device — connects, authentication
failures, pairings, revocations, subscriptions, rejected calls — plus a per-client
count of method calls. Arguments are **not** recorded: the log is there to show who
did how much, not to become a second copy of your messages. Nothing leaves the phone.

## Reporting a problem

Open an issue describing the class of problem. Please don't include working exploits,
tokens or message contents.
