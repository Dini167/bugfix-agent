package pager

import (
	"reflect"
	"testing"
)

var items = []string{"a", "b", "c", "d", "e"}

func TestFirstPage(t *testing.T) {
	if got := Page(items, 1, 2); !reflect.DeepEqual(got, []string{"a", "b"}) {
		t.Fatalf("got %v", got)
	}
}

func TestLastPartialPage(t *testing.T) {
	if got := Page(items, 3, 2); !reflect.DeepEqual(got, []string{"e"}) {
		t.Fatalf("got %v", got)
	}
}

func TestOutOfRange(t *testing.T) {
	if got := Page(items, 4, 2); got != nil {
		t.Fatalf("got %v", got)
	}
}

func TestPageCount(t *testing.T) {
	if got := PageCount(5, 2); got != 3 {
		t.Fatalf("got %d", got)
	}
}
