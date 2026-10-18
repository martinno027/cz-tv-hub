package cz.cztvhub.core

import cz.cztvhub.core.OfficialApp.Target
import org.junit.Assert.assertEquals
import org.junit.Test

class OfficialAppTest {
    @Test fun prefersInstalledAppThenStoreThenWeb() {
        assertEquals(Target.LAUNCH_APP, OfficialApp.choose(true, true, true))
        assertEquals(Target.STORE_APP, OfficialApp.choose(false, true, true))
        assertEquals(Target.STORE_WEB, OfficialApp.choose(false, false, true))
        assertEquals(Target.NONE, OfficialApp.choose(false, false, false))
    }

    @Test fun storeLinksPointAtCtPackage() {
        assertEquals("market://details?id=cz.motion.ivysilani", OfficialApp.MARKET_URL)
        assertEquals("https://play.google.com/store/apps/details?id=cz.motion.ivysilani", OfficialApp.WEB_URL)
    }
}
