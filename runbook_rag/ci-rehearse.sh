#!/usr/bin/env bash
set -euo pipefail

evidence_dir="${1:?Provide an evidence directory}"
mkdir -p "$evidence_dir"
targets="$evidence_dir/targets.json"
echo '{}' > "$targets"
registry=localhost:5000
docker run -d --name runbook-registry -p 127.0.0.1:5000:5000 registry:2
export RUNBOOK_ACCESS_TOKEN
RUNBOOK_ACCESS_TOKEN="$(openssl rand -hex 32)"

revision_file=runbook_rag/release_revision.txt
port=18080
for role in stable stale-candidate corrected-candidate; do
  commit=''
  while read -r revision; do
    if [[ "$(git show "$revision:$revision_file")" == "$role" ]]; then
      commit="$revision"
      break
    fi
  done < <(git log --format=%H HEAD -- "$revision_file")
  test -n "$commit"
  image="$registry/runbook:$commit"
  git archive "$commit" | docker build -f runbook_rag/Dockerfile \
    --label "org.opencontainers.image.revision=$commit" -t "$image" -
  docker push "$image"
  reference="$(docker image inspect "$image" --format '{{index .RepoDigests 0}}')"
  digest="${reference##*@}"
  docker run -d --name "runbook-$role" -p "127.0.0.1:$port:8000" \
    -e RUNBOOK_ACCESS_TOKEN -e "RUNBOOK_BUILD_COMMIT=$commit" \
    -e "RUNBOOK_IMAGE_DIGEST=$digest" "$reference"
  if ! curl --fail --silent --show-error --max-time 3 --retry 15 --retry-all-errors --retry-delay 1 \
    "http://127.0.0.1:$port/health"; then
    docker logs "runbook-$role"
    exit 1
  fi
  jq --arg role "$role" --arg sha "$commit" --arg digest "$digest" --arg port "$port" \
    '.[$role] = {url:("http://127.0.0.1:" + $port + "/evaluate"),
      build:{commit_sha:$sha,image_digest:$digest}}' "$targets" > "$targets.tmp"
  mv "$targets.tmp" "$targets"
  port=$((port + 1))
done

python -m runbook_rag.rehearse "$targets" > "$evidence_dir/gate.json"
for role in stable stale-candidate corrected-candidate; do
  docker logs "runbook-$role" > "$evidence_dir/$role.log" 2>&1
done
