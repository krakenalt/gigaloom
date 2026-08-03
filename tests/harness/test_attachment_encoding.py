import pytest

from gigaloom.attachments import (
    TextAttachmentDecodeError,
    decode_attachment_text,
)


@pytest.mark.parametrize(
    ("charset", "bom", "codec"),
    (
        ("utf-8-sig", b"\xef\xbb\xbf", "utf-8"),
        ("utf-16-le", b"\xff\xfe", "utf-16-le"),
        ("utf-16-be", b"\xfe\xff", "utf-16-be"),
        ("utf-32-le", b"\xff\xfe\x00\x00", "utf-32-le"),
        ("utf-32-be", b"\x00\x00\xfe\xff", "utf-32-be"),
    ),
)
def test_decodes_bom_variants_exactly(charset, bom, codec):
    text = "Привет, GigaLoom!\n"

    result = decode_attachment_text(bom + text.encode(codec))

    assert result.text == text
    assert result.charset == charset
    assert result.confidence_class == "exact"
    assert result.bom_present is True
    assert "�" not in result.text


@pytest.mark.parametrize("codec", ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"))
def test_decodes_conservative_bomless_unicode(codec):
    text = "# Комментарий\nprint('hello')\n"

    result = decode_attachment_text(text.encode(codec))

    assert result.text == text
    assert result.charset == codec
    assert result.confidence_class in {"strong", "conservative"}
    assert result.bom_present is False


@pytest.mark.parametrize("codec", ("windows-1251", "koi8-r"))
def test_decodes_legacy_russian_text_with_round_trip(codec):
    text = "Привет мир, это проверка текста и комментария.\n"

    result = decode_attachment_text(text.encode(codec))

    assert result.text == text
    assert result.charset == codec
    assert result.confidence_class in {"strong", "conservative"}
    assert result.text.encode(codec) == text.encode(codec)


def test_utf8_fast_path_is_exact_and_preserves_text():
    text = "hello, мир\nprint('ok')\n"

    result = decode_attachment_text(text.encode())

    assert result.text == text
    assert result.charset == "utf-8"
    assert result.confidence_class == "exact"
    assert result.bom_present is False


@pytest.mark.parametrize(
    "payload",
    (
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
        b"PK\x03\x04" + b"archive bytes",
        b"plain\x00text\x00masquerade",
        b"\xff\xfe\x00",
        bytes(range(256)),
    ),
)
def test_rejects_binary_or_malformed_payload_without_replacement(payload):
    with pytest.raises(TextAttachmentDecodeError):
        decode_attachment_text(payload)
