package lubabs770.mmm.modemizer.service

import android.content.Context
import java.io.File

/** Shell commands as root, when root is there. MMM works without it. */
object Root {

    data class Result(val code: Int, val output: String) {
        val ok: Boolean get() = code == 0
    }

    /** Cheap check: is there an `su` binary at all? */
    val installed: Boolean by lazy {
        System.getenv("PATH")?.split(':')?.any { File(it, "su").canExecute() } == true ||
            listOf("/sbin/su", "/system/bin/su", "/system/xbin/su", "/debug_ramdisk/su")
                .any { File(it).exists() }
    }

    /** Runs [command] through `su -c` and waits for it. */
    fun run(command: String, timeoutMs: Long = 10_000): Result {
        if (!installed) return Result(-1, "no su binary")
        return try {
            val process = ProcessBuilder("su", "-c", command)
                .redirectErrorStream(true)
                .start()
            // Read on a thread of its own: readText() only returns once su exits, so
            // reading here first would make the timeout below unreachable.
            var output = ""
            val reader = Thread {
                output = runCatching { process.inputStream.bufferedReader().use { it.readText() } }
                    .getOrDefault("")
            }.apply {
                isDaemon = true
                start()
            }
            val finished = waitFor(process, timeoutMs)
            if (!finished) {
                process.destroy()
                Result(-1, "timed out: $command")
            } else {
                reader.join(1_000)
                Result(process.exitValue(), output.trim())
            }
        } catch (e: Exception) {
            Result(-1, e.message ?: e.javaClass.simpleName)
        }
    }

    /** Confirms root actually works, not just that `su` exists. */
    fun granted(): Boolean = run("id -u").let { it.ok && it.output.trim() == "0" }

    /** `Process.waitFor(timeout, unit)` needs API 26, so poll instead. */
    private fun waitFor(process: Process, timeoutMs: Long): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            try {
                process.exitValue()
                return true
            } catch (e: IllegalThreadStateException) {
                Thread.sleep(50)
            }
        }
        return false
    }
}

/** Is MMM installed as a privileged system app? */
object Privileged {

    /**
     * The path alone is not enough: an update installed over the priv-app (say with
     * `adb install -r -g`) lives under /data/app yet stays privileged. A system app
     * holding MODIFY_PHONE_STATE, which only privileged apps are granted, is one.
     */
    fun isPrivApp(context: Context): Boolean {
        val info = context.applicationInfo
        val onPrivPath = info.sourceDir?.startsWith("/system/priv-app") == true ||
            info.sourceDir?.startsWith("/system_ext/priv-app") == true
        val updatedSystem = (info.flags and android.content.pm.ApplicationInfo.FLAG_SYSTEM) != 0 &&
            holds(context, android.Manifest.permission.MODIFY_PHONE_STATE)
        return onPrivPath || updatedSystem
    }

    fun holds(context: Context, permission: String): Boolean =
        context.packageManager.checkPermission(permission, context.packageName) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED
}
