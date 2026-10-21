# Security model

**MMM gives full control of the phone to anyone holding a valid full-scope token.**
That is the design, not an oversight: the whole point is to reach all of Android.
Everything below follows from taking that seriously.

## What protects the port

- **Loopback only.** The listener binds `127.0.0.1`. Nothing on the LAN can reach
  it; the Status screen shows exactly what it is bound to.
- **Remote access borrows someone else's authentication.** An SSH tunnel or
  `adb forward` is the only way in from another machine, and both authenticate
  before any MMM traffic exists.
- **A token on top.** The first message on every connection must be `auth`, and a
  wrong token closes the connection and is logged.

On a rooted phone, any app running as root can reach a loopback port, and any app
with your token can use it. MMM does not defend against code you have already given
root to.

## Tokens

- 32 random bytes from `SecureRandom`, shown once as hex.
- Only the SHA-256 is stored, in app-private storage, so a copy of the app's data
  cannot be turned back into a working token. Comparison is constant-time.
- The client id shown in the UI is its own random value, revealing nothing about the
  token.
- Revoking one takes effect immediately for new connections and drops its durable
  subscriptions.
- The client stores tokens in `~/.config/mmm/config.toml`, mode `600`. Treat that
  file the way you treat an SSH private key.

## Scopes

A token is `full` or `scoped`. A scoped token carries an allow-list of
`class#method` patterns and of broadcast actions, checked **in the app** on every
`call`, `new`, `get`, `set` and `subscribe`. The client cannot widen it; a modified
client gains nothing.

Scoped tokens cannot create proxies, because a Java listener can reach whatever the
listener's own API exposes.

The assistant preset points at one class, `Facade`, whose methods are the curated
operations. That is deliberate: a short allow-list is one you can actually read.

## Signing

Release APKs are signed with a key kept in CI secrets and **never committed**. A
leaked signing key would let anyone sign an "update" that replaces a privileged app
holding SMS, contacts and call-control permissions. Debug builds use a different key,
so a debug APK cannot silently upgrade a release install.

## Privileged permissions

The Magisk module installs MMM as a priv-app together with a generated allowlist
(`privapp-permissions-mmm.xml`). This is the only way to hold `MODIFY_PHONE_STATE`
and `READ_PRIVILEGED_PHONE_STATE`.

The allowlist is generated from the manifest at build time, because on Android 9 and
later with `ro.control_privapp_permissions=enforce` a priv-app requesting a
privileged permission it is not allowlisted for **can stop the phone from booting**.
A hand-written file that falls behind the manifest is a bricked boot; a generated one
cannot fall behind.

## What is recorded

The Activity screen keeps a rolling log on the device — connects, authentication
failures, pairings, revocations, subscriptions, rejected calls — plus a per-client
count of method calls. Arguments are **not** recorded: the log is there to show who
did how much, not to become a second copy of your messages. Nothing leaves the phone.

## Reporting a problem

Open an issue describing the class of problem. Please don't include working exploits,
tokens or message contents.
