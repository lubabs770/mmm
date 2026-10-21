package lubabs770.mmm.modemizer

import lubabs770.mmm.modemizer.rpc.HandleSink
import lubabs770.mmm.modemizer.rpc.Marshal
import lubabs770.mmm.modemizer.util.B64
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class Table : HandleSink {
    val held = HashMap<Long, Any>()
    private var next = 1L
    override fun handle(value: Any): Long {
        val id = next++
        held[id] = value
        return id
    }

    fun resolve(id: Long): Any = held.getValue(id)
}

@Suppress("unused")
private class Point(val x: Int, val y: Int) {
    fun getLabel(): String = "point"
    fun isOrigin(): Boolean = x == 0 && y == 0
}

private enum class Colour { RED, GREEN }

class MarshalTest {

    private val table = Table()

    private fun back(json: Any?): Any? = Marshal.fromJson(json, table::resolve)

    @Test
    fun `plain values cross as json`() {
        assertEquals(JSONObject.NULL, Marshal.toJson(null, table))
        assertEquals(true, Marshal.toJson(true, table))
        assertEquals("hi", Marshal.toJson("hi", table))
        assertEquals(7L, Marshal.toJson(7, table))
        assertEquals(7L, Marshal.toJson(7L, table))
        assertEquals(1.5, Marshal.toJson(1.5, table))
        assertEquals("c", Marshal.toJson('c', table))
    }

    @Test
    fun `numbers round trip`() {
        assertEquals(7L, back(Marshal.toJson(7, table)))
        assertEquals(1.5, back(Marshal.toJson(1.5, table)))
    }

    @Test
    fun `byte arrays round trip through base64`() {
        val bytes = byteArrayOf(0, 1, 2, 127, -128, -1)
        val json = Marshal.toJson(bytes, table) as JSONObject
        assertEquals(B64.encode(bytes), json.getString("\$b64"))
        assertArrayEquals(bytes, back(json) as ByteArray)
    }

    @Test
    fun `base64 handles every length`() {
        for (size in 0..16) {
            val bytes = ByteArray(size) { (it * 7 - 3).toByte() }
            assertArrayEquals(bytes, B64.decode(B64.encode(bytes)))
        }
    }

    @Test
    fun `lists and maps of plain values round trip`() {
        val value = listOf(1, "two", true, null, listOf(3))
        val json = Marshal.toJson(value, table) as JSONArray
        assertEquals(5, json.length())
        val restored = back(json) as List<*>
        assertEquals(listOf(1L, "two", true, null, listOf(1L * 3)), restored)

        val map = mapOf("a" to 1, "b" to listOf("x"))
        val restoredMap = back(Marshal.toJson(map, table)) as Map<*, *>
        assertEquals(1L, restoredMap["a"])
        assertEquals(listOf("x"), restoredMap["b"])
    }

    @Test
    fun `anything else becomes a handle that resolves back to the same object`() {
        val point = Point(2, 3)
        val json = Marshal.toJson(point, table) as JSONObject
        assertEquals(Point::class.java.name, json.getString("cls"))
        assertTrue(json.has("\$h"))
        assertTrue(back(json) === point)
    }

    @Test
    fun `without a sink a value becomes an opaque marker instead of a handle`() {
        // Durable events outlive the session their handles would belong to.
        val json = Marshal.toJson(Point(1, 1), null) as JSONObject
        assertEquals(Point::class.java.name, json.getString("\$opaque"))
        assertTrue(json.has("repr"))
    }

    @Test
    fun `enums carry their name alongside the handle`() {
        val json = Marshal.toJson(Colour.GREEN, table) as JSONObject
        assertEquals("GREEN", json.getString("name"))
        assertTrue(back(json) === Colour.GREEN)
    }

    @Test
    fun `classes cross as a class reference and resolve back`() {
        val json = Marshal.toJson(String::class.java, table) as JSONObject
        assertEquals("java.lang.String", json.getString("\$cls"))
        assertEquals(String::class.java, back(json))
    }

    @Test
    fun `arrays become json arrays`() {
        val json = Marshal.toJson(arrayOf("a", "b"), table) as JSONArray
        assertEquals("b", json.getString(1))
        val ints = Marshal.toJson(intArrayOf(4, 5), table) as JSONArray
        assertEquals(5L, ints.getLong(1))
    }

    @Test
    fun `expand snapshots a plain object field by field`() {
        val json = Marshal.expand(Point(0, 0), null) as JSONObject
        assertEquals(Point::class.java.name, json.getString("\$pojo"))
        assertEquals(0L, json.getLong("x"))
        assertEquals("point", json.getString("label"))
        assertEquals(true, json.getBoolean("origin"))
    }

    @Test
    fun `args decode in order`() {
        val args = JSONArray(listOf(1, "two", JSONObject().put("\$b64", B64.encode(byteArrayOf(9)))))
        val decoded = Marshal.argsFromJson(args, table::resolve)
        assertEquals(3, decoded.size)
        assertEquals(1, decoded[0])
        assertEquals("two", decoded[1])
        assertArrayEquals(byteArrayOf(9), decoded[2] as ByteArray)
    }

    @Test
    fun `missing args means no args`() {
        assertEquals(emptyList<Any?>(), Marshal.argsFromJson(null, table::resolve))
    }

    @Test
    fun `json null decodes to null`() {
        assertNull(back(JSONObject.NULL))
    }

    @Test
    fun `non-finite doubles cross as markers instead of breaking the reply`() {
        val nan = Marshal.toJson(Double.NaN, table) as JSONObject
        assertEquals("NaN", nan.getString(Marshal.KEY_FLOAT))
        val inf = Marshal.toJson(Float.NEGATIVE_INFINITY, table) as JSONObject
        assertEquals("-Infinity", inf.getString(Marshal.KEY_FLOAT))
        // Inside an array too, where JSONArray.put would otherwise throw.
        JSONArray().put(Marshal.toJson(doubleArrayOf(1.0, Double.POSITIVE_INFINITY), table)).toString()
        assertTrue((back(nan) as Double).isNaN())
        assertEquals(Double.NEGATIVE_INFINITY, back(inf))
        assertEquals(1.5, Marshal.toJson(1.5, table))
    }
}
