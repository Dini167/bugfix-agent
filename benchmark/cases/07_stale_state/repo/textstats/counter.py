"""Counting words across documents."""


class WordCounter:
    counts: dict[str, int] = {}

    def add_text(self, text: str) -> None:
        for word in text.lower().split():
            self.counts[word] = self.counts.get(word, 0) + 1

    def most_common(self, n: int = 1) -> list[tuple[str, int]]:
        return sorted(self.counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]

    def total(self) -> int:
        return sum(self.counts.values())
