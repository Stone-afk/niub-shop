from agent_service.response_language import detect_language, language_instruction


def test_detects_chinese_and_english_messages() -> None:
    assert detect_language("请推荐一个杯子") == "zh"
    assert detect_language("Please recommend a mug") == "en"


def test_mixed_or_empty_input_uses_previous_language_then_chinese() -> None:
    assert detect_language("mug 杯子", previous="en") == "en"
    assert detect_language("mug 杯子", previous="zh") == "zh"
    assert detect_language("", previous=None) == "zh"


def test_language_instruction_forbids_unsolicited_translation() -> None:
    assert "Reply only in English" in language_instruction("en")
    assert "只使用中文" in language_instruction("zh")
