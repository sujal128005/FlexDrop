#!/usr/bin/env bash
# Smoke test against a running FlexDrop server.
#   FLEXDROP_URL=http://127.0.0.1:8000 FLEXDROP_KEY=<admin key> bash scripts/smoke_test.sh
set -euo pipefail
URL="${FLEXDROP_URL:-http://127.0.0.1:8000}"
KEY="${FLEXDROP_KEY:?set FLEXDROP_KEY to an admin API key}"
H=(-H "X-API-Key: ${KEY}" -H "Content-Type: application/json")
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
LATER=$(date -u -d "+8 minutes" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v+8M +%Y-%m-%dT%H:%M:%SZ)
SUFFIX=$(date +%s)

curl -fsS "${URL}/health" && echo
curl -fsS "${URL}/ready" && echo
curl -fsS "${H[@]}" -X POST "${URL}/v1/stores" -d '{"id":"SMOKE-S","name":"Smoke Kitchen","category":"FOOD","location":{"lat":12.9352,"lng":77.6245},"base_prep_min":8,"zone":"smoke"}' >/dev/null
curl -fsS "${H[@]}" -X POST "${URL}/v1/partners" -d "{\"id\":\"SMOKE-P\",\"location\":{\"lat\":12.9360,\"lng\":77.6250},\"zone\":\"smoke\",\"available_at\":\"${NOW}\"}" >/dev/null
for i in 1 2; do
  curl -fsS "${H[@]}" -X POST "${URL}/v1/orders" -d "{\"id\":\"SMOKE-${SUFFIX}-${i}\",\"store_id\":\"SMOKE-S\",\"customer_id\":\"c${i}\",\"drop\":{\"lat\":12.9460,\"lng\":77.6270},\"placed_at\":\"${NOW}\",\"handling\":{\"temp_classes\":[\"HOT\"],\"volume_l\":5,\"weight_kg\":1}}" >/dev/null
done
PLANS=$(curl -fsS "${H[@]}" -X POST "${URL}/v1/optimize" -d "{\"zone\":\"smoke\",\"as_of\":\"${LATER}\"}")
echo "optimize: ${PLANS}" | cut -c1-300
curl -fsS "${H[@]}" "${URL}/v1/metrics" | cut -c1-300 && echo
echo "smoke test OK"
