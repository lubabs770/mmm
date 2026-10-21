package lubabs770.mmm.modemizer.util

import android.util.Log
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import org.json.JSONObject

/**
 * Small JSON documents in app-private storage, replaced atomically.
 *
 * Everything MMM persists — settings, tokens, the audit log, durable subscriptions —
 * is a handful of kilobytes of structured JSON, so a file per document in `filesDir`
 * is the whole storage layer.
 */
object JsonFile {

    private const val TAG = "MmmJsonFile"

    fun read(file: File): JSONObject? {
        if (!file.exists()) return null
        return try {
            JSONObject(file.readText())
        } catch (e: Exception) {
            Log.w(TAG, "unreadable ${file.name}, ignoring it", e)
            null
        }
    }

    /**
     * Replaces [file] with [obj]. Writers of the same file are serialised, and each
     * uses its own temporary file, so two threads saving at once can never interleave
     * into a corrupt document.
     */
    fun write(file: File, obj: JSONObject) {
        val text = obj.toString()
        synchronized(lockFor(file)) {
            var tmp: File? = null
            try {
                val dir = file.absoluteFile.parentFile ?: return
                dir.mkdirs()
                tmp = File.createTempFile("${file.name}.", ".tmp", dir)
                FileOutputStream(tmp).use { out ->
                    out.write(text.toByteArray(Charsets.UTF_8))
                    out.fd.sync()
                }
                // rename(2) replaces the target atomically on the same filesystem.
                if (!tmp.renameTo(file)) throw IOException("rename to ${file.name} failed")
                tmp = null
            } catch (e: Exception) {
                Log.e(TAG, "could not save ${file.name}", e)
            } finally {
                tmp?.delete()
            }
        }
    }

    private val locks = ConcurrentHashMap<String, Any>()

    private fun lockFor(file: File): Any = locks.getOrPut(file.absolutePath) { Any() }
}
