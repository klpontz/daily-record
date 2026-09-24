from unittest.mock import MagicMock, patch
import pytest
from classify_log import extract_bullets, classify_items, format_breakdown, main


def test_classify_items_empty_makes_no_api_call():
    with patch("classify_log.TypeSafeClient") as mock_class:
        result = classify_items("key", [])

    assert result == []
    mock_class.assert_not_called()


def _mock_choice_result(answers: dict[str, tuple[str, float]]):
    result = MagicMock()
    result.choices = {k: MagicMock(choice=c, confidence=conf) for k, (c, conf) in answers.items()}
    return result


def test_classify_items_maps_category_and_confidence_per_item():
    items = ["Debugged the DFEO bug", "Monitored the hiring inbox"]

    with patch("classify_log.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_choice_result(
            {"item_0": ("product_qa", 0.95), "item_1": ("ops_noise", 0.4)}
        )

        result = classify_items("key", items)

    assert result == [
        {"text": "Debugged the DFEO bug", "category": "product_qa", "confidence": 0.95},
        {"text": "Monitored the hiring inbox", "category": "ops_noise", "confidence": 0.4},
    ]


def test_classify_items_embeds_each_item_text_in_its_own_question():
    items = ["First item text", "Second item text"]

    with patch("classify_log.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_choice_result(
            {"item_0": ("product_qa", 0.9), "item_1": ("product_qa", 0.9)}
        )

        classify_items("key", items)

    questions = mock_client.system_one.call_args.args[1]
    assert questions["item_0"].instructions["item"] == "First item text"
    assert questions["item_1"].instructions["item"] == "Second item text"


def test_classify_items_raises_on_api_failure():
    with patch("classify_log.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.side_effect = Exception("TypeSafe down")

        with pytest.raises(Exception, match="TypeSafe down"):
            classify_items("key", ["some item"])


def test_extract_bullets_pulls_from_done_only():
    text = (
        "### Plan\n- [ ] ignored\n\n"
        "### Done\n- Fixed the thing\n- Shipped the other thing\n\n"
        "### Slack\n- #training: discussed something\n\n"
        "### Asana\n- **Project** — Task: did it.\n"
    )
    assert extract_bullets(text) == ["Fixed the thing", "Shipped the other thing"]


def test_extract_bullets_empty_log_returns_empty_list():
    text = "### Done\n- \n\n### Slack\n- \n"
    assert extract_bullets(text) == []


def test_extract_bullets_missing_done_section_returns_empty_list():
    assert extract_bullets("### Plan\n- a\n") == []


def test_format_breakdown_groups_and_computes_percentages():
    items = [
        {"text": "Debugged the DFEO bug", "category": "product_qa", "confidence": 0.95},
        {"text": "Wrote the questionnaire spec", "category": "product_qa", "confidence": 0.9},
        {"text": "Coached Ajmal in the 1:1", "category": "ops_transferable", "confidence": 0.8},
        {"text": "Monitored the hiring inbox", "category": "ops_noise", "confidence": 0.85},
    ]

    result = format_breakdown(items)

    assert "- Product/QA (~50%):" in result
    assert "    Debugged the DFEO bug" in result
    assert "    Wrote the questionnaire spec" in result
    assert "- Ops (transferable, ~25%):" in result
    assert "    Coached Ajmal in the 1:1" in result
    assert "- Ops (noise, ~25%):" in result
    assert "    Monitored the hiring inbox" in result


def test_format_breakdown_flags_low_confidence_items():
    items = [{"text": "Ambiguous item", "category": "ops_transferable", "confidence": 0.45}]

    result = format_breakdown(items)

    assert "    Ambiguous item (low confidence — check by hand)" in result


def test_format_breakdown_shows_zero_percent_categories():
    items = [{"text": "Only product work", "category": "product_qa", "confidence": 0.9}]

    result = format_breakdown(items)

    assert "- Ops (transferable, ~0%):" in result
    assert "- Ops (noise, ~0%):" in result


def test_format_breakdown_empty_returns_no_items_message():
    assert format_breakdown([]) == "No items to classify."


def test_main_empty_log_prints_no_items_message(tmp_path, capsys):
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Done\n- \n\n### Slack\n- \n")

    with patch.dict("classify_log.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
        exit_code = main([str(log_file)])

    assert exit_code == 0
    assert "No items to classify." in capsys.readouterr().out


def test_main_classifies_and_prints_breakdown(tmp_path, capsys):
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Done\n- Debugged the thing\n")

    with patch("classify_log.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.return_value = _mock_choice_result({"item_0": ("product_qa", 0.9)})

        with patch.dict("classify_log.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
            exit_code = main([str(log_file)])

    assert exit_code == 0
    assert "- Product/QA (~100%):" in capsys.readouterr().out


def test_main_nonexistent_log_path_returns_1_with_clear_message(capsys):
    """Fix 3: a bad log path must not raise a raw FileNotFoundError."""
    with patch.dict("classify_log.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
        exit_code = main(["/nonexistent/path/2026-09-24.md"])

    assert exit_code == 1
    assert "Could not read log file" in capsys.readouterr().err


def test_main_missing_api_key_prints_clear_message(tmp_path, capsys):
    """Fix 3: a missing TYPESAFE_API_KEY must produce a config-specific message, not a raw KeyError."""
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Done\n- Debugged the thing\n")

    with patch("classify_log.load_dotenv"):
        with patch.dict("classify_log.os.environ", {}, clear=True):
            exit_code = main([str(log_file)])

    assert exit_code == 1
    assert "TYPESAFE_API_KEY is not set" in capsys.readouterr().err


def test_main_api_failure_prints_clear_message_and_exits_1(tmp_path, capsys):
    log_file = tmp_path / "2026-09-24.md"
    log_file.write_text("### Done\n- Debugged the thing\n")

    with patch("classify_log.TypeSafeClient") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.system_one.side_effect = Exception("TypeSafe down")

        with patch.dict("classify_log.os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
            exit_code = main([str(log_file)])

    assert exit_code == 1
    assert "Classification failed" in capsys.readouterr().err
