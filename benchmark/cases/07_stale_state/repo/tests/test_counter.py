from textstats.counter import WordCounter


def test_counts_words():
    counter = WordCounter()
    counter.add_text("the cat and the hat")
    assert counter.most_common(1) == [("the", 2)]


def test_counters_are_independent():
    a = WordCounter()
    a.add_text("alpha beta")
    b = WordCounter()
    b.add_text("gamma")
    assert b.total() == 1
    assert a.total() == 2


def test_new_counter_is_empty():
    assert WordCounter().total() == 0
