//! Text previews for list views.

/// The first `max` characters of `s`, followed by "..." if anything was cut.
pub fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        return s.to_string();
    }
    format!("{}...", &s[..max])
}

/// Number of words, split on whitespace.
pub fn word_count(s: &str) -> usize {
    s.split_whitespace().count()
}
