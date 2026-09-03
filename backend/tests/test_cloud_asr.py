from app.services.asr import extract_dashscope_sentences


def test_extract_dashscope_sentences_combines_punctuated_results():
    value = [
        {"text": "小时候，家门口有一条河。"},
        {"text": "夏天我们总去河边。"},
    ]
    assert extract_dashscope_sentences(value) == "小时候，家门口有一条河。夏天我们总去河边。"


def test_extract_dashscope_sentences_accepts_single_sentence_and_ignores_noise():
    assert extract_dashscope_sentences({"text": " 我还记得。 "}) == "我还记得。"
    assert extract_dashscope_sentences({"begin_time": 0}) == ""
