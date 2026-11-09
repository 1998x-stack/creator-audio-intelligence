import pytest
from creator_audio_intel.security import validate_profile_url,video_id_from_url


def test_profile_requires_https_author_homepage():
    assert validate_profile_url('https://www.douyin.com/user/a123')
    for url in ('http://www.douyin.com/user/a','https://douyin.com.evil.site/user/a',
                'https://www.douyin.com/video/7275916556641652027',
                'https://user:pw@www.douyin.com/user/a'):
        with pytest.raises(ValueError):validate_profile_url(url)


def test_video_id():
    assert video_id_from_url('https://www.douyin.com/video/7275916556641652027')=='7275916556641652027'
    with pytest.raises(ValueError):video_id_from_url('https://evil.site/video/7275916556641652027')
