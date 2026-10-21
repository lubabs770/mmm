package lubabs770.mmm.modemizer.util

import android.util.Log
import java.io.File
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

    fun write(file: File, obj: JSONObject) {
        try {
            file.parentFile?.mkdirs()
            val tmp = File(file.parentFile, "${file.name}.tmp")
            tmp.writeText(obj.toString())
            // rename(2) replaces the target atomically on the same filesystem.
            if (!tmp.renameTo(file)) {
                file.writeText(tmp.readText())
                tmp.delete()
            }
        } catch (e: Exception) {
            Log.e(TAG, "could not save ${file.name}", e)
        }
    }
}
