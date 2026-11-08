from app import db
from app.guard import check_draft
from app.memory import Memory


def make_memory(tmp_path):
    path = tmp_path / "t.db"
    db.init(path)
    return Memory(path)


def test_search_brand_finds_match_and_survives_punctuation(tmp_path):
    m = make_memory(tmp_path)
    m.seed_brand([("Living Twin", "The Living Twin syncs with the user.", "https://x.test"),
                  ("Privacy", "Privacy first personalisation.", "https://x.test")])
    assert "syncs" in m.search_brand("living twin?! (agents) AND \"quote")
    assert m.search_brand("") == ""


def test_search_brand_is_capped(tmp_path):
    m = make_memory(tmp_path)
    m.seed_brand([("A", "memory " * 2000, "https://x.test")])
    assert len(m.search_brand("memory")) <= 1500


def test_newest_learning_replaces_older_for_same_key(tmp_path):
    m = make_memory(tmp_path)
    m.remember("Zero-Prompt AI/linkedin", "too hypey", "human")
    m.remember("Zero-Prompt AI/linkedin", "needs a concrete example", "human")
    text = m.get_learnings()
    assert "concrete example" in text and "hypey" not in text


def test_guard_flags_length_banned_and_unsupported_numbers():
    assert check_draft("Hello there", "linkedin", "") == []
    assert any("length" in p for p in check_draft("x" * 2201, "instagram", ""))
    assert any("banned" in p for p in check_draft("The world's only twin", "linkedin", ""))
    assert any("number" in p for p in check_draft("Boosts focus by 40%", "linkedin", ""))
    assert check_draft("A 40% rise was reported", "linkedin", "study found a 40% rise") == []
