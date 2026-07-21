#!/usr/bin/env bash
# Runs ON a Linux GitHub Actions runner (has epoll -> massdns is fast). Resolves one .com shard and
# fingerprints Shopify-hosted domains (A record in 23.227.38.0/24). Output: hits.txt (one domain/line).
set -uo pipefail
SHARD="$1"
# build massdns with epoll (fast on Linux)
git clone --depth 1 https://github.com/blechschmidt/massdns /tmp/md >/dev/null 2>&1
make -C /tmp/md >/dev/null 2>&1
# fetch this shard's domain list from the release
gh release download census-com --repo "$GITHUB_REPOSITORY" -p "com-${SHARD}.gz" -D /tmp 2>/dev/null
gunzip -c "/tmp/com-${SHARD}.gz" > /tmp/shard.txt
n_in=$(wc -l < /tmp/shard.txt)
# resolve (high concurrency, 30 retries across resolvers for recall) + fingerprint Shopify
/tmp/md/bin/massdns -r resolvers_clean.txt -t A -o S -s 10000 -c 30 -w /tmp/out.txt /tmp/shard.txt 2>/tmp/md.err
grep ' A 23.227.38.' /tmp/out.txt | sed 's/\.\{0,1\} A .*//' | tr 'A-Z' 'a-z' | sort -u > hits.txt
resolved=$(grep -c ' A ' /tmp/out.txt || echo 0)
# recall guard: domains that got NO answer at all (SERVFAIL/timeout) -> record for a second pass
grep -oE '^[^ ]+' /tmp/out.txt | tr 'A-Z' 'a-z' | sed 's/\.$//' | sort -u > /tmp/answered.txt
tr 'A-Z' 'a-z' < /tmp/shard.txt | sort -u > /tmp/allin.txt
comm -23 /tmp/allin.txt /tmp/answered.txt > unanswered.txt
echo "shard $SHARD: in=$n_in resolved=$resolved shopify=$(wc -l < hits.txt) unanswered=$(wc -l < unanswered.txt)"