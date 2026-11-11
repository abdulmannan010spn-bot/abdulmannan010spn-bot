#!/usr/bin/env bash
# Writes data/github.json: the last year of contributions plus pinned repos
# (and your top public repos as a fallback). Needs the GitHub CLI (`gh`) and jq.
# Usage: scripts/fetch_data.sh [login]
set -euo pipefail
cd "$(dirname "$0")/.."
LOGIN="${1:-${GITHUB_REPOSITORY_OWNER:-abdulmannan010spn-bot}}"
mkdir -p data

QUERY='
fragment R on Repository {
  name description url stargazerCount forkCount primaryLanguage { name color }
}
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
    pinnedItems(first: 6, types: REPOSITORY) { nodes { ... on Repository { ...R } } }
    repositories(first: 6, privacy: PUBLIC, ownerAffiliations: OWNER, isFork: false,
                 orderBy: {field: STARGAZERS, direction: DESC}) { nodes { ...R } }
  }
}'

repo='{name, description, url, stars: .stargazerCount, forks: .forkCount,
       language: .primaryLanguage.name, color: .primaryLanguage.color}'

gh api graphql -f login="$LOGIN" -f query="$QUERY" \
| jq -e "
  .data.user as \$u | {
    login: \"$LOGIN\",
    total: \$u.contributionsCollection.contributionCalendar.totalContributions,
    weeks: [\$u.contributionsCollection.contributionCalendar.weeks[]
            | [.contributionDays[] | {date, count: .contributionCount}]],
    pinned: [\$u.pinnedItems.nodes[] | select(. != null) | $repo],
    top:    [\$u.repositories.nodes[] | $repo]
  }" > data/github.json.tmp
mv data/github.json.tmp data/github.json
echo "wrote data/github.json"
