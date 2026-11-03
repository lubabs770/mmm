# Screen tab source

`screen.js` here is bundled by esbuild into `../mmm/ui/screen.js`, which is
committed so `pip install` never needs Node.

    npm ci && npm run build

`../mmm/ui/scrcpy-server` is the official scrcpy server v3.3.3, the newest
version `@yume-chan/scrcpy` 2.3.0 supports:
https://github.com/Genymobile/scrcpy/releases/download/v3.3.3/scrcpy-server-v3.3.3
sha256 `7e70323ba7f259649dd4acce97ac4fefbae8102b2c6d91e2e7be613fd5354be0`
(matches the GitHub release asset digest). Bump it together with the Tango
packages and `AdbScrcpyOptions3_3_3` in `screen.js`.
