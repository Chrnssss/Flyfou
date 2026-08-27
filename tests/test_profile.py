"""Profiles saved by an older Flyfou must still load, and still save cleanly.

The monster templates were dropped from the schema. Every profile on disk still
has a `templates.monsters` list in it, so the risk is a load that throws or a
save that quietly loses the hunting ground. Both are checked against the real
profiles first, then against a round-trip in a scratch directory.

    .venv\\Scripts\\python tests\\test_profile.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(name, got, want):
    ok = got == want
    print("  %-52s %s" % (name, "ok" if ok else "FAILED  got %r want %r" % (got, want)))
    if not ok:
        failures.append(name)


from flyfou.profile import ProfileStore  # noqa: E402  (after sys.path)

print("profiles already on disk still open")
existing = ProfileStore()
names = existing.names()
if not names:
    print("  (none saved yet — nothing to check)")
loaded = None
for name in names:
    loaded = existing.load(name)
    print("  %-20s hunting ground %s, home %s, %s"
          % (name, loaded.play_area.as_list(), "yes" if loaded.home else "no",
             "; ".join(loaded.problems()) or "ready"))
check("an old profile has no monsters left on it", hasattr(loaded, "monsters"), False)

print("\nsaving and re-reading keeps everything")
scratch = tempfile.mkdtemp(prefix="flyfou-test-")
try:
    os.environ["FLYFOU_HOME"] = scratch
    store = ProfileStore()
    profile = loaded
    profile.name = "roundtrip"
    store.save(profile)
    back = store.load("roundtrip")
    check("hunting ground survives", back.play_area.as_list(), profile.play_area.as_list())
    check("match threshold survives", back.match_threshold, profile.match_threshold)
    check("attack key survives", back.attack_key, profile.attack_key)
    check("target bar survives", back.target_hp.rect.as_list(), profile.target_hp.rect.as_list())
    check("nothing invalid about it", back.problems(), profile.problems())
finally:
    os.environ.pop("FLYFOU_HOME", None)
    shutil.rmtree(scratch, ignore_errors=True)

print("\n%s" % ("all checks passed" if not failures else "FAILED: %s" % ", ".join(failures)))
sys.exit(1 if failures else 0)
