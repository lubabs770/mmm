# Install

Three pieces, each optional except the first: the **app**, the **Magisk module**, and
the **Python client**.

## The app

Download `mmm-<version>.apk` from the
[releases page](https://github.com/lubabs770/mmm/releases) and install it:

```sh
adb install -r mmm-0.1.0.apk
# or copy it to the phone and tap it
```

Release APKs are universal — one file for arm64-v8a, armeabi-v7a, x86 and x86_64.
MMM writes no native code itself; the AndroidX libraries it uses ship every ABI, and
every build checks that all four are still present, so one APK keeps covering every
device.

Then open MMM:

1. **Status** — turn the service on. It shows `127.0.0.1:7357`.
2. **Permissions** — grant what you need. SMS, phone state, call log and contacts are
   requested; nothing is needed for the RPC engine itself.
3. **Clients** — pair a client and copy the token.

The service runs in the foreground with a notification, which is what keeps Android
from killing the socket. It starts again on boot unless you turn that off in
Settings.

### Builds from CI

Every push builds a debug APK as a workflow artifact (`mmm-debug-apk`). Debug and
release builds are signed with different keys, so one will not upgrade the other:
uninstall first when switching.

## The Magisk module (optional)

`mmm-magisk-<version>.zip` installs the same APK as a **priv-app**:

```
/system/priv-app/MMM/MMM.apk
/system/etc/permissions/privapp-permissions-mmm.xml
```

Flash it in the Magisk app and reboot. The Status screen then shows *Priv-app: yes*,
and privileged calls — answering and ending calls through `TelecomManager`, reading
privileged phone state — start working.

**Then install the APK once more, over the module, with `-g`:**

```sh
adb install -r -g mmm-<version>.apk
```

Android 10 and later treat reading and receiving SMS as *restricted* permissions. An
app that arrives only through the module never gets them, and `pm grant` cannot
change that, so SMS events (and anything built on them, like `mmm watch` or rules)
silently never fire. Installing with `-g` through adb exempts them. If the app is
ever reset to the module's copy (say, "uninstall updates"), run it again. See
[#3](https://github.com/lubabs770/mmm/issues/3).

The allowlist XML is **generated from the app manifest** during the build
(`magisk/gen_privapp_xml.py`). This matters: on Android 9+ with
`ro.control_privapp_permissions=enforce`, a priv-app that requests a privileged
permission without being allowlisted can stop the phone from booting. Generating it
means the manifest and the allowlist cannot drift apart.

Without the module, MMM still works as a normal app. Only privileged calls fail.

## The Python client

```sh
pip install "git+https://github.com/lubabs770/mmm#subdirectory=client"
```

The client is released **separately from the app**, on `client-v*` tags: each
**MMM client** release on the releases page carries a wheel and a `.pyz`. Its version
moves on its own; any client works with any app that speaks protocol 1.

```sh
pipx install mmm-<version>-py3-none-any.whl   # or pip install
```

It has no dependencies, which is the point: it installs on Termux, where wheels are
scarce, and on any Linux, macOS or Windows box with Python 3.10 or newer.

There is also a single-file build, `mmm-client-<version>.pyz`, for places where you
would rather not install anything:

```sh
python mmm-client-0.3.0.pyz status
```

### On the phone, in Termux

```sh
pkg install python
pip install "git+https://github.com/lubabs770/mmm#subdirectory=client"
mmm pair <token>
mmm status
```

### On a computer

Pick a transport ([pairing](pairing.md) has the details):

```sh
mmm pair <token> --ssh phone --ssh-port 8022   # through Termux's sshd
mmm pair <token> --adb                         # over USB
```
