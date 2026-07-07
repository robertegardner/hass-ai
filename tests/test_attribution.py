from pae.ingest.attribution import AUTOMATION, MANUAL, PAE, ContextCache, attribute


def test_physical_action_is_manual():
    # wall switch: no user_id, no parent, context unseen
    assert attribute("ctx1", None, None, ContextCache()) == MANUAL


def test_ui_action_is_manual():
    # dashboard tap: user_id set, no parent
    assert attribute("ctx1", None, "user-abc", ContextCache()) == MANUAL


def test_automation_context_id_match():
    cache = ContextCache()
    cache.add("auto-ctx", AUTOMATION)
    assert attribute("auto-ctx", None, None, cache) == AUTOMATION


def test_automation_parent_id_match():
    # service call ran in a child context of the automation's context
    cache = ContextCache()
    cache.add("auto-ctx", AUTOMATION)
    assert attribute("child-ctx", "auto-ctx", None, cache) == AUTOMATION


def test_unseen_parent_is_automation_not_manual():
    # something non-human caused this even though we missed its announcement;
    # never credit it as a human action
    assert attribute("child-ctx", "mystery-parent", None, ContextCache()) == AUTOMATION


def test_unseen_parent_with_user_id_still_automation():
    # automations triggered by a user action inherit the user_id; the parent
    # chain wins over user_id
    cache = ContextCache()
    cache.add("auto-ctx", AUTOMATION)
    assert attribute("child-ctx", "auto-ctx", "user-abc", cache) == AUTOMATION


def test_pae_context_wins():
    cache = ContextCache()
    cache.add("pae-ctx", PAE)
    assert attribute("pae-ctx", None, None, cache) == PAE
    assert attribute("child", "pae-ctx", None, cache) == PAE


def test_cache_ttl_expiry():
    cache = ContextCache(ttl_seconds=10)
    cache.add("auto-ctx", AUTOMATION, now=1000.0)
    assert attribute("auto-ctx", None, None, cache, now=1005.0) == AUTOMATION
    # after expiry the context no longer counts; no parent -> manual
    assert attribute("auto-ctx", None, None, cache, now=1011.0) == MANUAL
