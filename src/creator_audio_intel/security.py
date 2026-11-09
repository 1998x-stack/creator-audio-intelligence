"""Source URL validation and stable identifier checks.

Two platforms are supported:

  douyin   the production target: HTTPS only, `douyin.com` hosts.
  fixture  the local verification site (scripts/serve_fixture.py). It uses a
           distinct host and a distinct `platform` value in SQLite, so a
           fixture run can never be mistaken for a Douyin run.
"""
import re
from urllib.parse import urlsplit

DOUYIN_HOSTS = {"www.douyin.com", "douyin.com"}
FIXTURE_HOSTS = {"127.0.0.1", "localhost", "fixture.local"}
PROFILE_HOSTS = DOUYIN_HOSTS  # backwards-compatible alias
PLATFORMS = ("douyin", "fixture")

_VIDEO = re.compile(r"/(?:video|m/video)/([0-9]{12,25})(?:/|$)")
_ID = re.compile(r"^[0-9]{12,25}$")
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _allowed_hosts(platform: str) -> set[str]:
    if platform == "douyin":
        return DOUYIN_HOSTS
    if platform == "fixture":
        return FIXTURE_HOSTS
    raise ValueError(f"Unknown platform: {platform}")


def _check(url: str, platform: str):
    p = urlsplit(url)
    hosts = _allowed_hosts(platform)
    if p.hostname not in hosts or p.username or p.password:
        raise ValueError(f"Not a supported {platform} URL")
    if platform == "douyin" and p.port not in (None, 443):
        raise ValueError("Douyin URLs must use the default HTTPS port")
    if p.scheme == "https":
        pass
    elif p.scheme == "http" and platform == "fixture" and p.hostname in _LOOPBACK:
        pass
    else:
        raise ValueError(f"{platform} URLs must be HTTPS (loopback fixture may use HTTP)")
    return p


def validate_profile_url(url: str, platform: str = "douyin") -> str:
    p = _check(url, platform)
    if not p.path.startswith("/user/"):
        raise ValueError("Expected a creator homepage /user/...")
    if len(p.path) <= len("/user/"):
        raise ValueError("Creator homepage has no user identifier")
    return url


def video_id_from_url(url: str, platform: str = "douyin") -> str:
    p = _check(url, platform)
    match = _VIDEO.search(p.path)
    if not match:
        raise ValueError("No numeric video id in URL")
    return match.group(1)


def validate_video_id(video_id: str) -> str:
    if not _ID.fullmatch(video_id):
        raise ValueError("Invalid platform video ID")
    return video_id
