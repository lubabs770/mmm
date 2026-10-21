package lubabs770.mmm.modemizer

import lubabs770.mmm.modemizer.rpc.HandleTable
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class HandleTableTest {

    @Test
    fun `a handle resolves back to the same object`() {
        val table = HandleTable()
        val value = Any()
        val handle = table.put(value)
        assertSame(value, table.get(handle))
        assertSame(value, table.require(handle))
    }

    @Test
    fun `handles are distinct and never reused`() {
        val table = HandleTable()
        val first = table.put(Any())
        table.release(first)
        val second = table.put(Any())
        assertTrue(second > first)
    }

    @Test
    fun `release frees the object and reports whether it existed`() {
        val table = HandleTable()
        val handle = table.put(Any())
        assertTrue(table.release(handle))
        assertTrue(!table.release(handle))
        assertNull(table.get(handle))
        assertEquals(0, table.size)
    }

    @Test
    fun `releasing a batch counts the ones that existed`() {
        val table = HandleTable()
        val handles = (1..3).map { table.put(Any()) }
        assertEquals(3, table.releaseAll(handles + 999L))
        assertEquals(0, table.size)
    }

    @Test
    fun `an unknown handle is an error, not a null`() {
        val table = HandleTable()
        val error = runCatching { table.require(42L) }.exceptionOrNull()
        assertTrue(error is NoSuchElementException)
    }

    @Test
    fun `the cap stops a runaway client`() {
        val table = HandleTable(cap = 2)
        table.put(Any())
        table.put(Any())
        val error = runCatching { table.put(Any()) }.exceptionOrNull()
        assertTrue(error is HandleTable.CapExceeded)
        assertEquals(2, table.size)
    }

    @Test
    fun `clear drops everything the session held`() {
        val table = HandleTable()
        repeat(5) { table.put(Any()) }
        table.clear()
        assertEquals(0, table.size)
    }

    @Test
    fun `two tables never hand out the same number`() {
        // A reconnecting client may still hold handles from its old session; they must
        // be unknown to the new one, not name some other object.
        val old = HandleTable()
        val new = HandleTable()
        val fromOld = (1..5).map { old.put(Any()) }.toSet()
        val fromNew = (1..5).map { new.put(Any()) }.toSet()
        assertTrue(fromOld.intersect(fromNew).isEmpty())
        fromOld.forEach { assertNull(new.get(it)) }
    }
}
