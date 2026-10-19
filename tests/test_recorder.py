import pytest

from radcast.recorder import StreamInfo, choose_format, minimum_size

LC = StreamInfo(codec="aac", profile="LC", bitrate=320000, sample_rate=48000, channels=2)
HE = StreamInfo(codec="aac", profile="HE-AAC", bitrate=96000, sample_rate=48000, channels=2)
MP3 = StreamInfo(codec="mp3", profile=None, bitrate=128000, sample_rate=44100, channels=2)
OPUS = StreamInfo(codec="opus", profile=None, bitrate=None, sample_rate=48000, channels=2)


@pytest.mark.parametrize("info, mode, copy, ext", [
    (LC, "auto", True, "m4a"),
    (MP3, "auto", True, "mp3"),
    (HE, "auto", False, "m4a"),        # the silent-start case: never copied in auto
    (OPUS, "auto", False, "m4a"),
    (None, "auto", False, "m4a"),      # probe failed: play it safe
    (HE, "copy", True, "m4a"),         # explicit copy is honoured for AAC
    (OPUS, "copy", False, "m4a"),      # but never for codecs podcast apps can't play
    (LC, "transcode", False, "m4a"),
])
def test_choose_format(info, mode, copy, ext):
    fmt = choose_format(info, mode, "128k")
    assert fmt.copy is copy and fmt.ext == ext
    assert fmt.codec_args == (["-c:a", "copy"] if copy else ["-c:a", "aac", "-b:a", "128k"])
    assert fmt.reason


def test_describe():
    assert LC.describe() == "AAC-LC, 320 kbps, 48 kHz, stereo"
    assert HE.describe() == "HE-AAC, 96 kbps, 48 kHz, stereo"
    assert MP3.describe() == "MP3, 128 kbps, 44.1 kHz, stereo"
    assert "2 variants" in StreamInfo("aac", "LC", None, None, None, variants=2).describe()
    assert HE.is_he_aac and not LC.is_he_aac


def test_minimum_size_scales_with_duration():
    assert minimum_size(5) == 16 * 1024          # a 5s test can't be held to a 100KB bar
    assert minimum_size(3 * 3600) == 3 * 3600 * 2048   # a 3h show well under 16 kbps is junk
