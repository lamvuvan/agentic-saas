"""Unit tests for Vietnamese NLP utilities — 50 test cases.

Tests: normalize_text(), strip_honorifics(), words_to_numbers(), extract_product_note()
Must FAIL before order_agent/vn_utils.py is implemented.
"""

import pytest
from order_agent.vn_utils import (
    extract_product_note,
    normalize_text,
    strip_honorifics,
    words_to_numbers,
)


# ---------------------------------------------------------------------------
# normalize_text — 15 cases
# ---------------------------------------------------------------------------

class TestNormalizeText:
    def test_basic_lowercase(self):
        assert normalize_text("XIN CHÀO") == "xin chào"

    def test_strip_leading_whitespace(self):
        assert normalize_text("  xin chào") == "xin chào"

    def test_strip_trailing_whitespace(self):
        assert normalize_text("xin chào  ") == "xin chào"

    def test_unicode_nfc_normalization(self):
        # NFC: precomposed form should be preserved/normalized
        text = "xin cha\u0300o"  # decomposed 'à'
        result = normalize_text(text)
        assert "xa\u00e0o" in result or "chào" in result  # either form normalized

    def test_multiple_spaces_preserved(self):
        result = normalize_text("hai  trứng")
        assert result == "hai  trứng"

    def test_mixed_case(self):
        assert normalize_text("Hai Trứng Lộn") == "hai trứng lộn"

    def test_numbers_preserved(self):
        assert normalize_text("bàn 3") == "bàn 3"

    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_only_whitespace(self):
        assert normalize_text("   ") == ""

    def test_english_text(self):
        assert normalize_text("Hello World") == "hello world"

    def test_special_chars_preserved(self):
        result = normalize_text("1.000đ")
        assert "1.000đ" in result

    def test_newlines_stripped(self):
        result = normalize_text("\nanh lâm\n")
        assert "\n" not in result

    def test_tab_stripped(self):
        result = normalize_text("anh\tlâm")
        assert result == "anh\tlâm" or "\t" not in result  # implementation choice

    def test_diacritics_preserved(self):
        result = normalize_text("Nguyễn Văn A")
        assert "nguyễn văn a" == result

    def test_punctuation_preserved(self):
        result = normalize_text("Xin chào, anh!")
        assert "xin chào, anh!" == result


# ---------------------------------------------------------------------------
# strip_honorifics — 15 cases
# ---------------------------------------------------------------------------

class TestStripHonorifics:
    def test_strip_anh(self):
        assert strip_honorifics("anh Lâm") == "Lâm"

    def test_strip_chi(self):
        assert strip_honorifics("chị Mai") == "Mai"

    def test_strip_em(self):
        assert strip_honorifics("em Hoa") == "Hoa"

    def test_strip_bac(self):
        assert strip_honorifics("bác Tám") == "Tám"

    def test_strip_co(self):
        assert strip_honorifics("cô Lan") == "Lan"

    def test_strip_chu(self):
        assert strip_honorifics("chú Hùng") == "Hùng"

    def test_strip_ong(self):
        assert strip_honorifics("ông Nam") == "Nam"

    def test_strip_ba(self):
        assert strip_honorifics("bà Liên") == "Liên"

    def test_no_honorific_unchanged(self):
        assert strip_honorifics("Lâm") == "Lâm"

    def test_empty_string(self):
        assert strip_honorifics("") == ""

    def test_only_honorific(self):
        result = strip_honorifics("anh")
        assert result.strip() == "" or result == "anh"  # either strip or leave

    def test_honorific_in_middle_not_stripped(self):
        result = strip_honorifics("Nguyễn anh Hùng")
        # "anh" in middle — should not strip the leading name
        assert "Nguyễn" in result

    def test_leading_space_handled(self):
        result = strip_honorifics("  anh Lâm")
        assert "Lâm" in result

    def test_name_with_space(self):
        result = strip_honorifics("anh Nguyễn Văn A")
        assert "Nguyễn Văn A" in result

    def test_lowercase_honorific(self):
        result = strip_honorifics("ANH Lâm")
        # May or may not strip uppercase — implementation choice
        assert "Lâm" in result


# ---------------------------------------------------------------------------
# words_to_numbers — 12 cases
# ---------------------------------------------------------------------------

class TestWordsToNumbers:
    def test_mot(self):
        assert words_to_numbers("một phở") == "1 phở"

    def test_hai(self):
        assert words_to_numbers("hai trứng lộn") == "2 trứng lộn"

    def test_ba(self):
        assert words_to_numbers("ba cháo lòng") == "3 cháo lòng"

    def test_bon(self):
        assert words_to_numbers("bốn bò kho") == "4 bò kho"

    def test_nam(self):
        assert words_to_numbers("năm bánh mì") == "5 bánh mì"

    def test_sau(self):
        assert words_to_numbers("sáu ly cà phê") == "6 ly cà phê"

    def test_bay(self):
        assert words_to_numbers("bảy cái") == "7 cái"

    def test_tam(self):
        assert words_to_numbers("tám lon") == "8 lon"

    def test_chin(self):
        assert words_to_numbers("chín cái kẹo") == "9 cái kẹo"

    def test_muoi(self):
        assert words_to_numbers("mười ly") == "10 ly"

    def test_muoi_mot(self):
        assert words_to_numbers("mười một cái") == "11 cái"

    def test_muoi_hai(self):
        assert words_to_numbers("mười hai cái bánh") == "12 cái bánh"


# ---------------------------------------------------------------------------
# extract_product_note — 8 cases
# ---------------------------------------------------------------------------

class TestExtractProductNote:
    def test_it_duong(self):
        product, note = extract_product_note("cà phê ít đường")
        assert "cà phê" in product
        assert "ít đường" in note

    def test_them_da(self):
        product, note = extract_product_note("trà đá thêm đá")
        assert "trà đá" in product
        assert "thêm đá" in note

    def test_no_note(self):
        product, note = extract_product_note("trứng lộn")
        assert "trứng lộn" in product
        assert note == "" or note is None

    def test_khong_duong(self):
        product, note = extract_product_note("cà phê không đường")
        assert "cà phê" in product
        assert "không đường" in note

    def test_nong(self):
        product, note = extract_product_note("trà nóng")
        assert "trà" in product or "trà nóng" in product

    def test_lanh(self):
        product, note = extract_product_note("nước cam lạnh")
        assert "nước cam" in product or "nước cam lạnh" in product

    def test_empty_string(self):
        result = extract_product_note("")
        assert result is not None  # Should not raise

    def test_complex_note(self):
        product, note = extract_product_note("phở bò ít nước béo thêm hành")
        assert "phở bò" in product
        assert len(note) > 0
