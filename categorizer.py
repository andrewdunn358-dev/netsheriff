"""Our own domain categorization — independent of NxFilter's paid
categorization services. See build_categories.py for how the lookup is
built and why this exists.
"""
import json


def load(path="categories.json"):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def categorize(domain, lookup):
    """Check the exact hostname first, then progressively shorter parent
    domains (so 'mail.google.com' matches its own specific entry before
    ever falling back to a broader 'google.com' entry). Returns a category
    code (e.g. 'sns', 'shopping') or None if nothing matched — callers
    should fall back to NxFilter's own category in that case."""
    if not domain:
        return None
    domain = domain.lower().strip(".")
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in lookup:
            return lookup[candidate]
    return None
