"""Public-page URL validation and stable identifier checks."""
import re
from urllib.parse import urlsplit

PROFILE_HOSTS = {"www.douyin.com", "douyin.com"}
_VIDEO = re.compile(r"/(?:video|m/video)/([0-9]{12,25})(?:/|$)")
_ID = re.compile(r"^[0-9]{12,25}$")


def validate_profile_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in PROFILE_HOSTS or p.port not in (None, 443) or p.username or p.password:
        raise ValueError("Profile must be an HTTPS douyin.com page")
    if not p.path.startswith("/user/"):
        raise ValueError("Expected a Douyin creator homepage /user/...")
    return url


def video_id_from_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in PROFILE_HOSTS or p.port not in (None, 443) or p.username or p.password:
        raise ValueError("Not a supported Douyin video URL")
    match = _VIDEO.search(p.path)
    if not match:
        raise ValueError("No numeric video id in URL")
    return match.group(1)


def validate_video_id(video_id: str) -> str:
    if not _ID.fullmatch(video_id):
        raise ValueError("Invalid platform video ID")
    return video_id
