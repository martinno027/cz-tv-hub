"""Command line entry point: ``python -m cztvhub {serve,playlist,check,dvb-setup,dvb-fake}``."""
import argparse
import signal
import sys
import tempfile
import threading
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from .api import USER_AGENT, StreamError, fetch_live_streams, fetch_schedules, fetch_vod_url
from .channels import BY_ID, CHANNELS
from .config import ConfigError, load_config
from .m3u import build_playlist
from .server import serve


def _config(args):
    try:
        return load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _sigterm_as_interrupt():
    """systemd stops with SIGTERM: unwind like Ctrl+C so ffmpeg children and temp HLS are cleaned up."""
    def handler(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handler)


def cmd_serve(args):
    cfg = _config(args)
    gateway = None
    if cfg.dvb.enabled:
        from .dvb.gateway import DvbGateway

        gateway = DvbGateway(cfg)
    _sigterm_as_interrupt()
    serve(args.host or cfg.host, args.port or cfg.port, gateway=gateway)
    return 0


def cmd_dvb_setup(args):
    from .dvb.setup import run_setup
    from .dvb.tvheadend import TvheadendClient, TvhError

    cfg = _config(args)
    tvh = cfg.tvheadend
    try:
        run_setup(TvheadendClient(tvh.url, tvh.user, tvh.password), cfg.dvb, dry_run=args.dry_run)
    except (TvhError, RuntimeError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_dvb_fake(args):
    from .dvb.fake import FakeTvheadend

    fake = FakeTvheadend(Path(args.data_dir), args.host, args.port, tuners=args.tuners, user=args.user,
                         password=args.password, clip_seconds=args.clip_seconds)
    _sigterm_as_interrupt()
    fake.start()
    print(f"fake TVHeadend on {fake.url} ({args.tuners} tuners, data in {args.data_dir})", flush=True)
    print("[tvheadend] for the gateway config:", f"url = {fake.url}", f"user = {args.user}",
          f"password = {args.password}", sep="\n", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        fake.stop()
    return 0


def cmd_playlist(args):
    sys.stdout.write(build_playlist(args.base_url))
    return 0


def _is_hls(url: str) -> bool:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read(200).decode("utf-8", "replace").startswith("#EXTM3U")


def _report(ok: bool, what: str, detail: str) -> bool:
    print(f"{'OK  ' if ok else 'FAIL'} {what:24} {detail}")
    return ok


def cmd_check(_args):
    """Resolve every channel (live + timeshift) and one archive item. Exit 1 on any failure."""
    failures = 0
    for ch in CHANNELS:
        try:
            streams = fetch_live_streams(ch.id)
            ok = _is_hls(streams.main)
            detail = "OK" if ok else "unexpected body"
            if ok and streams.timeshift:
                detail += ", timeshift " + ("OK" if _is_hls(streams.timeshift) else "unexpected body")
            elif ok:
                detail += ", no timeshift"
        except (StreamError, OSError) as exc:
            ok, detail = False, str(exc)
        failures += not _report(ok, f"{ch.id} {ch.name}", detail)

    # Archive: yesterday's first playable programme on CT24 (news is rarely rights-restricted).
    ch = BY_ID["CH_24"]
    yesterday = date.today() - timedelta(days=1)
    try:
        programmes = fetch_schedules([ch.slug], yesterday).get(ch.slug, [])
        item = next((p for p in programmes if p.playable), None)
        if item is None:
            ok, detail = False, f"no playable programme in {len(programmes)} entries"
        else:
            ok = _is_hls(fetch_vod_url(item.idec))
            detail = f"{item.title} ({item.idec}) " + ("OK" if ok else "unexpected body")
    except (StreamError, OSError) as exc:
        ok, detail = False, str(exc)
    failures += not _report(ok, f"archive {ch.name} {yesterday}", detail)
    return 1 if failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cztvhub", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the playlist/redirect server")
    p_serve.add_argument("--config", help="INI config, e.g. /etc/cztvhub/cztvhub.conf (enables DVB if configured)")
    p_serve.add_argument("--host", help="bind address (default: config or 0.0.0.0)")
    p_serve.add_argument("--port", type=int, help="port (default: config or 8098)")
    p_serve.set_defaults(func=cmd_serve)

    p_pl = sub.add_parser("playlist", help="print an M3U to stdout")
    p_pl.add_argument("--base-url", required=True, help="e.g. http://192.168.1.50:8098")
    p_pl.set_defaults(func=cmd_playlist)

    p_check = sub.add_parser("check", help="verify live, timeshift and archive streams resolve and play")
    p_check.set_defaults(func=cmd_check)

    p_setup = sub.add_parser("dvb-setup", help="configure TVHeadend DVR retention and autorec rules (idempotent)")
    p_setup.add_argument("--config", required=True)
    p_setup.add_argument("--dry-run", action="store_true", help="only print what would change")
    p_setup.set_defaults(func=cmd_dvb_setup)

    p_fake = sub.add_parser("dvb-fake", help="fake TVHeadend with test streams for development")
    p_fake.add_argument("--host", default="127.0.0.1")
    p_fake.add_argument("--port", type=int, default=9981)
    p_fake.add_argument("--tuners", type=int, default=2)
    p_fake.add_argument("--user", default="cztvhub")
    p_fake.add_argument("--password", default="fake")
    p_fake.add_argument("--clip-seconds", type=int, default=60, help="length of the fake finished recordings")
    p_fake.add_argument("--data-dir", default=str(Path(tempfile.gettempdir()) / "cztvhub-fake"))
    p_fake.set_defaults(func=cmd_dvb_fake)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
