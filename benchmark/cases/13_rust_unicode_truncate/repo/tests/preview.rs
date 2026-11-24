use preview::{truncate, word_count};

#[test]
fn short_text_is_unchanged() {
    assert_eq!(truncate("hello", 10), "hello");
}

#[test]
fn ascii_is_cut_with_ellipsis() {
    assert_eq!(truncate("hello world", 5), "hello...");
}

#[test]
fn hebrew_is_cut_by_characters() {
    assert_eq!(truncate("שלום עולם", 4), "שלום...");
}

#[test]
fn emoji_is_cut_by_characters() {
    assert_eq!(truncate("🙂🙂🙂🙂", 2), "🙂🙂...");
}

#[test]
fn counts_words() {
    assert_eq!(word_count("one  two three"), 3);
}
