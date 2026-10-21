package lubabs770.mmm.modemizer

import lubabs770.mmm.modemizer.rpc.Overloads
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Stands in for a framework class with the overloads reflection has to tell apart. */
@Suppress("unused")
class Fixture {
    fun take(value: Int) = "int"
    fun take(value: Long) = "long"
    fun take(value: Double) = "double"
    fun take(value: String) = "String"
    fun take(value: CharSequence) = "CharSequence"
    fun take(value: Any) = "Object"

    fun pair(a: String, b: Int) = "String,int"
    fun pair(a: String, b: String) = "String,String"

    fun bytes(data: ByteArray) = data.size
    fun ints(values: IntArray) = values.sum()

    fun ambiguous(value: Left) = "Left"
    fun ambiguous(value: Right) = "Right"

    interface Left
    interface Right
    class Both : Left, Right

    companion object {
        @JvmStatic
        fun double(value: Int) = value * 2
    }
}

class OverloadsTest {

    private val cls = Fixture::class.java

    private fun call(name: String, args: List<Any?>, sig: List<String>? = null): Any? {
        val method = Overloads.findMethod(cls, name, args, sig)
        val coerced = Overloads.coerceArgs(method.parameterTypes, args)
        return method.invoke(Fixture(), *coerced)
    }

    @Test
    fun `an integer argument picks int, not long`() {
        // JSON gives an Integer; both int and long accept it, int is the closer fit.
        assertEquals("int", call("take", listOf(7)))
    }

    @Test
    fun `a long argument picks long`() {
        assertEquals("long", call("take", listOf(7L)))
    }

    @Test
    fun `a double argument picks double`() {
        assertEquals("double", call("take", listOf(1.5)))
    }

    @Test
    fun `a string argument prefers String over CharSequence and Object`() {
        assertEquals("String", call("take", listOf("hi")))
    }

    @Test
    fun `sig forces a choice`() {
        assertEquals("long", call("take", listOf(7), listOf("long")))
        assertEquals("java.lang.Object", Overloads.typeNames(
            Overloads.findMethod(cls, "take", listOf(7), listOf("java.lang.Object")).parameterTypes
        ).single())
    }

    @Test
    fun `arity and types pick between two-argument overloads`() {
        assertEquals("String,int", call("pair", listOf("a", 1)))
        assertEquals("String,String", call("pair", listOf("a", "b")))
    }

    @Test
    fun `byte arrays pass through untouched`() {
        assertEquals(3, call("bytes", listOf(byteArrayOf(1, 2, 3))))
    }

    @Test
    fun `a json list becomes a typed int array`() {
        assertEquals(6, call("ints", listOf(listOf(1, 2, 3))))
    }

    @Test
    fun `coerce builds the array the parameter wants`() {
        val coerced = Overloads.coerce(IntArray::class.java, listOf(1, 2L, 3.9))
        assertArrayEquals(intArrayOf(1, 2, 3), coerced as IntArray)
    }

    @Test
    fun `an equally specific overload is refused and lists the candidates`() {
        val error = runCatching { call("ambiguous", listOf(Fixture.Both())) }.exceptionOrNull()
        assertTrue("expected Ambiguous, got $error", error is Overloads.Ambiguous)
        val candidates = (error as Overloads.Ambiguous).candidates
        assertEquals(2, candidates.size)
        assertTrue(candidates.any { it.contains("Left") })
        assertTrue(candidates.any { it.contains("Right") })
    }

    @Test
    fun `a missing method says so`() {
        val error = runCatching { call("nope", emptyList()) }.exceptionOrNull()
        assertTrue(error is Overloads.NoMatch)
    }

    @Test
    fun `no overload accepting the arguments says so`() {
        val error = runCatching { call("pair", listOf("a", "b", "c")) }.exceptionOrNull()
        assertTrue(error is Overloads.NoMatch)
    }

    @Test
    fun `static methods resolve on the class`() {
        val method = Overloads.findMethod(cls, "double", listOf(21), requireStatic = true)
        assertEquals(42, method.invoke(null, *Overloads.coerceArgs(method.parameterTypes, listOf(21))))
    }

    @Test
    fun `fits rejects null for a primitive`() {
        assertTrue(Overloads.fits(String::class.java, null))
        assertTrue(!Overloads.fits(java.lang.Integer.TYPE, null))
    }
}
