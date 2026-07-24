#!/usr/bin/env bash
set -uo pipefail
SHARD="$1"
gh release download "${RELEASE:-woo-verify}" --repo "$GITHUB_REPOSITORY" -p "com-${SHARD}.gz" -D /tmp 2>/dev/null
gunzip -c "/tmp/com-${SHARD}.gz" > /tmp/shard.txt
python3 woo_verify.py --resolved-out resolved.txt < /tmp/shard.txt > keepers.tsv 2> diag.txt || true
echo "shard $SHARD: $(wc -l < keepers.tsv) keepers of $(wc -l < /tmp/shard.txt)"; cat diag.txt
