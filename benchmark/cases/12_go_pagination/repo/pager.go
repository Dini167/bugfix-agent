// Package pager splits result lists into pages.
package pager

// Page returns the items on the given 1-based page. Out-of-range pages are empty.
func Page(items []string, page, size int) []string {
	if page < 1 || size < 1 {
		return nil
	}
	start := (page - 1) * size
	if start >= len(items) {
		return nil
	}
	end := start + size - 1
	if end > len(items) {
		end = len(items)
	}
	return items[start:end]
}

// PageCount is the number of pages needed for n items.
func PageCount(n, size int) int {
	if size < 1 {
		return 0
	}
	return (n + size - 1) / size
}
