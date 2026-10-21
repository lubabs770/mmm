package lubabs770.mmm.modemizer

import lubabs770.mmm.modemizer.auth.Scope
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ScopeTest {

    @Test
    fun `a full token may do anything`() {
        assertTrue(Scope.FULL.allowsMethod("android.telephony.SmsManager", "sendTextMessage"))
        assertTrue(Scope.FULL.allowsAction("anything.at.all"))
    }

    @Test
    fun `a scoped token is limited to its patterns`() {
        val scope = Scope(
            full = false,
            methods = listOf("android.telephony.TelephonyManager#get*", "android.os.Build#*"),
            actions = listOf("android.provider.Telephony.SMS_RECEIVED"),
        )
        assertTrue(scope.allowsMethod("android.telephony.TelephonyManager", "getSimState"))
        assertFalse(scope.allowsMethod("android.telephony.TelephonyManager", "setDataEnabled"))
        assertTrue(scope.allowsMethod("android.os.Build", "toString"))
        assertFalse(scope.allowsMethod("java.lang.Runtime", "exec"))
        assertTrue(scope.allowsAction("android.provider.Telephony.SMS_RECEIVED"))
        assertFalse(scope.allowsAction("android.intent.action.BOOT_COMPLETED"))
    }

    @Test
    fun `the assistant preset reaches the facade and nothing else`() {
        val scope = Scope.mcpPreset()
        assertFalse(scope.full)
        assertTrue(scope.allowsMethod(Scope.FACADE, "smsSend"))
        assertFalse(scope.allowsMethod("java.lang.Runtime", "exec"))
        assertFalse(scope.allowsMethod("android.app.ActivityManager", "killBackgroundProcesses"))
    }

    @Test
    fun `wildcards do not escape the dots they are written with`() {
        assertTrue(Scope.matches("android.*#get*", "android.os.Build#getRadioVersion"))
        assertFalse(Scope.matches("android.os.Build#*", "android.os.BuildHack#x"))
        assertTrue(Scope.matches("*", "whatever#x"))
    }

    @Test
    fun `scope survives a json round trip`() {
        val scope = Scope(full = false, methods = listOf("a#b"), actions = listOf("c"))
        val back = Scope.fromJson(scope.toJson())
        assertFalse(back.full)
        assertTrue(back.allowsMethod("a", "b"))
        assertTrue(back.allowsAction("c"))
    }
}
