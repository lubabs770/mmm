#!/system/bin/sh
# Magisk calls this during installation.
ui_print "- MMM: installing as a priv-app"
ui_print "  /system/priv-app/MMM/MMM.apk"
ui_print "  /system/etc/permissions/privapp-permissions-mmm.xml"
ui_print "- Reboot, then open MMM and pair a client."

set_perm_recursive "$MODPATH/system/priv-app" 0 0 0755 0644
set_perm_recursive "$MODPATH/system/etc/permissions" 0 0 0755 0644
