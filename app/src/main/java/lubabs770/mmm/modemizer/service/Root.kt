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
            val output = process.inputStream.bufferedReader().use { it.readText() }
            val finished = waitFor(process, timeoutMs)
            if (!finished) {
                process.destroy()
                Result(-1, "timed out: $command")
            } else {
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

    fun isPrivApp(context: Context): Boolean =
        context.applicationInfo.sourceDir?.startsWith("/system/priv-app") == true ||
            context.applicationInfo.sourceDir?.startsWith("/system_ext/priv-app") == true

    fun holds(context: Context, permission: String): Boolean =
        context.packageManager.checkPermission(permission, context.packageName) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED
}
