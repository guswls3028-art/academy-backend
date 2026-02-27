#!/bin/bash
# PATH: scripts/rewrite_history_remove_secrets.sh
# DEPLOY_COMMANDS.md 에서 AWS 키를 플레이스홀더로 치환한 뒤 히스토리 재작성.
# 실행: Git Bash 에서 cd /c/academy && bash scripts/rewrite_history_remove_secrets.sh
# 주의: main 히스토리가 바뀌므로, 푸시 시 git push --force origin main 필요.

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 실제 키 값은 레포에 넣지 않음. 패턴으로만 치환 (AKIA* / 40자 시크릿 할당 라인).
export FILTER_BRANCH_SQUELCH_WARNING=1
echo "Rewriting all commits on main (removing AWS key patterns from scripts/DEPLOY_COMMANDS.md)..."

git filter-branch -f --tree-filter '
  if [ -f scripts/DEPLOY_COMMANDS.md ]; then
    python -c "
import os, re
p = \"scripts/DEPLOY_COMMANDS.md\"
if os.path.isfile(p):
    with open(p, \"rb\") as f:
        c = f.read()
    # $env:AWS_ACCESS_KEY_ID = \"AKIA...\" -> placeholder
    c = re.sub(rb\"(\\\$env:AWS_ACCESS_KEY_ID\s*=\s*\\\")AKIA[A-Z0-9]{16}\", rb\"\1YOUR_AWS_ACCESS_KEY_ID\", c)
    # $env:AWS_SECRET_ACCESS_KEY = \"<40자>\" -> placeholder
    c = re.sub(rb\"(\\\$env:AWS_SECRET_ACCESS_KEY\s*=\s*\\\")[A-Za-z0-9/+=]{40}\", rb\"\1YOUR_AWS_SECRET_ACCESS_KEY\", c)
    with open(p, \"wb\") as f:
        f.write(c)
"
  fi
' main

echo "Done. Run: git push --force origin main"
echo "Then revoke the exposed keys in AWS IAM and create new ones."
