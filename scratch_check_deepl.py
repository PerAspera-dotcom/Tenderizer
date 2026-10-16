"""CR-012 §1 verification, round 2 — diagnose without ever printing the
actual key value: confirm the fix is deployed, whether the raw env var
actually had stray whitespace/quotes to clean, and the real DeepL response
body (DeepL's own error message, not just the status code) for a definitive
read on whether this is a formatting issue or a genuinely invalid/revoked
key.
"""
import os
import sys

sys.path.insert(0, "src")

import requests
import translate

raw = os.getenv("DEEPL_API_KEY")
print(f"has _clean_key: {hasattr(translate, '_clean_key')}")
print(f"raw key is None: {raw is None}")
if raw is not None:
    cleaned = translate._clean_key(raw)
    print(f"raw length: {len(raw)}, cleaned length: {len(cleaned)}, differs: {raw != cleaned}")
    print(f"raw repr of first/last 2 chars: {raw[:2]!r} ... {raw[-2:]!r}")
    print(f"ends with ':fx' (free tier) after cleaning: {cleaned.endswith(':fx')}")
    print(f"endpoint selected: {translate._usage_endpoint(cleaned)}")

    resp = requests.get(
        translate._usage_endpoint(cleaned),
        headers={"Authorization": f"DeepL-Auth-Key {cleaned}"},
        timeout=10,
    )
    print(f"direct call status: {resp.status_code}")
    print(f"direct call body: {resp.text[:300]!r}")
