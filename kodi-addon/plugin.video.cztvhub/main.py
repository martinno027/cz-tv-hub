"""Kodi entry point: maps cztvhub.plugin results onto Kodi list items.

All logic lives in cztvhub.plugin (unit-tested without Kodi); `kodi-addon/build.py`
bundles the cztvhub package into resources/lib.
"""
import os
import sys
import urllib.parse
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

import xbmc  # noqa: E402
import xbmcaddon  # noqa: E402
import xbmcgui  # noqa: E402
import xbmcplugin  # noqa: E402
import xbmcvfs  # noqa: E402

from cztvhub.api import StreamError  # noqa: E402
from cztvhub.guide import plugin_url, write_guide  # noqa: E402
from cztvhub.plugin import Directory, Message, Play, Plugin, RefreshGuide  # noqa: E402

HLS_MIME = "application/vnd.apple.mpegurl"


def set_info(item, title, plot):
    if hasattr(item, "getVideoInfoTag"):  # Kodi 20+
        tag = item.getVideoInfoTag()
        tag.setTitle(title)
        tag.setPlot(plot)
    else:
        item.setInfo("video", {"title": title, "plot": plot})


def show_directory(handle, result):
    xbmcplugin.setPluginCategory(handle, result.title)
    xbmcplugin.setContent(handle, "videos")
    for entry in result.items:
        item = xbmcgui.ListItem(entry.label, offscreen=True)
        set_info(item, entry.label, entry.plot)
        if entry.playable:
            item.setProperty("IsPlayable", "true")
        xbmcplugin.addDirectoryItem(handle, plugin_url(entry.path), item, isFolder=entry.folder)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def play(handle, result):
    item = xbmcgui.ListItem(result.title, path=result.url, offscreen=True)
    item.setMimeType(HLS_MIME)
    item.setContentLookup(False)
    if xbmc.getCondVisibility("System.HasAddon(inputstream.adaptive)"):
        item.setProperty("inputstream", "inputstream.adaptive")
        if result.from_buffer_start:
            item.setProperty("inputstream.adaptive.play_timeshift_buffer", "true")
    if result.start_offset:
        item.setProperty("StartOffset", str(int(result.start_offset)))
    xbmcplugin.setResolvedUrl(handle, True, item)


def guide_dir():
    return xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo("profile"))


def refresh_guide(handle):
    try:
        count = write_guide(guide_dir(), date.today())
        notify(f"TV průvodce aktualizován ({count} pořadů)")
    except StreamError as exc:
        notify(exc.user_message)
    xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))


def notify(text):
    xbmcgui.Dialog().notification("ČT", text, xbmcgui.NOTIFICATION_WARNING, 6000)


def main(argv):
    base, handle, query = argv[0], int(argv[1]), argv[2].lstrip("?")
    path = urllib.parse.urlsplit(base).path or "/"
    result = Plugin().route(path, dict(urllib.parse.parse_qsl(query)))
    is_playback = path.startswith("/play/") or path.startswith("/info")
    if isinstance(result, RefreshGuide):
        refresh_guide(handle)
    elif isinstance(result, Directory):
        show_directory(handle, result)
    elif isinstance(result, Play):
        play(handle, result)
    elif isinstance(result, Message):
        notify(result.text)
        if is_playback:
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
        else:
            xbmcplugin.endOfDirectory(handle, succeeded=False)


if __name__ == "__main__":
    main(sys.argv)
