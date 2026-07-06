from ai_service.formats import Segment, format_timecode, to_full_text, to_plain_text


def test_format_timecode():
    assert format_timecode(0.0) == "[00:00:00]"
    assert format_timecode(4.9) == "[00:00:04]"      # truncated, not rounded
    assert format_timecode(3661.5) == "[01:01:01]"


def test_to_full_text():
    segments = [
        Segment(id=0, start=0.0, end=4.2, text=" Добрый день, компания Аэроклуб."),
        Segment(id=1, start=4.2, end=9.87, text=" Здравствуйте, я по поводу брони."),
    ]
    assert to_full_text(segments) == (
        "[00:00:00] Добрый день, компания Аэроклуб.\n"
        "[00:00:04] Здравствуйте, я по поводу брони."
    )


def test_to_full_text_skips_blank_segments_and_empty_list():
    assert to_full_text([]) == ""
    assert to_full_text([Segment(id=0, start=0.0, end=1.0, text="  ")]) == ""


def test_to_plain_text():
    segments = [
        Segment(id=0, start=0.0, end=4.2, text=" первая реплика"),
        Segment(id=1, start=4.2, end=9.87, text=" вторая реплика"),
    ]
    assert to_plain_text(segments) == "первая реплика вторая реплика"
