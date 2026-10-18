"""Kodi background service: keeps the TV guide files (M3U + XMLTV) fresh.

Runs on the viewer's device, so no server is needed. PVR IPTV Simple Client
reads the files from this add-on's profile folder (see docs/kodi-setup.md).
"""
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

import xbmc  # noqa: E402
import xbmcaddon  # noqa: E402
import xbmcvfs  # noqa: E402

from cztvhub.api import StreamError  # noqa: E402
from cztvhub.guide import EPG_NAME, write_guide  # noqa: E402

REFRESH_SECONDS = 6 * 3600
CHECK_SECONDS = 15 * 60  # also the retry delay when CT or the network is unreachable


def is_stale(path):
    try:
        return time.time() - os.path.getmtime(path) > REFRESH_SECONDS
    except OSError:
        return True


def main():
    monitor = xbmc.Monitor()
    directory = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo("profile"))
    while not monitor.abortRequested():
        if is_stale(os.path.join(directory, EPG_NAME)):
            try:
                count = write_guide(directory, date.today())
                xbmc.log(f"[cztvhub] TV guide written: {count} programmes", xbmc.LOGINFO)
            except (StreamError, OSError) as exc:
                xbmc.log(f"[cztvhub] TV guide refresh failed: {exc}", xbmc.LOGWARNING)
        if monitor.waitForAbort(CHECK_SECONDS):
            break


if __name__ == "__main__":
    main()
