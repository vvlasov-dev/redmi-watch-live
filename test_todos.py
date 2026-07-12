"""Todos CRUD + ordering against an in-memory DB."""
from core import store
store.init(":memory:")
from features.todos import engine   # noqa: E402

FAILED = 0


def check(name, cond):
    global FAILED
    print(("  OK   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED += 1


# empty start
check("starts empty", engine.all() == [])

# add trims + ignores blank
r = engine.add("  buy milk  ")
check("add returns ok+id", r["ok"] and r["id"])
check("add trims text", engine.all()[0]["text"] == "buy milk")
check("blank add rejected", engine.add("   ")["ok"] is False)
check("still one todo after blank", len(engine.all()) == 1)

# order: second add goes after the first
engine.add("call bank")
ids = [t["id"] for t in engine.all()]
check("two todos in add order", [t["text"] for t in engine.all()] == ["buy milk", "call bank"])

# toggle moves done items to the bottom (ORDER BY done, ord)
engine.toggle(ids[0])
lst = engine.all()
check("toggled marked done", any(t["id"] == ids[0] and t["done"] for t in lst))
check("done has done_ts", [t for t in lst if t["id"] == ids[0]][0]["done_ts"] is not None)
check("open item now first", lst[0]["text"] == "call bank")
check("done item sorts last", lst[-1]["text"] == "buy milk")
check("toggle unknown id => not found", engine.toggle(9999)["ok"] is False)

# edit
engine.edit(ids[1], "call the bank")
check("edit applied", [t for t in engine.all() if t["id"] == ids[1]][0]["text"] == "call the bank")

# reorder open list
engine.add("water plants")
open_ids = [t["id"] for t in engine.all() if not t["done"]]
engine.reorder(list(reversed(open_ids)))
new_open = [t["text"] for t in engine.all() if not t["done"]]
check("reorder reverses open order", new_open == ["water plants", "call the bank"])

# open_top caps + excludes done
check("open_top excludes done", all(not t["done"] for t in engine.open_top(5)))
check("open_top caps at n", len(engine.open_top(1)) == 1)

# delete
engine.delete(ids[0])
check("delete removes row", all(t["id"] != ids[0] for t in engine.all()))

print("\n%s" % ("ALL TODOS TESTS PASSED" if not FAILED else "%d TODOS TEST(S) FAILED" % FAILED))
import sys
sys.exit(1 if FAILED else 0)
