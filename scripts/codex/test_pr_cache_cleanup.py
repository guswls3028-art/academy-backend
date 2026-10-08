"""Execute the actual cache workflow shell against a fake, fail-closed gh API."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/pr-cache-cleanup.yml'
BASH = shutil.which('bash')
if os.name == 'nt' and shutil.which('git'):
    git_bash = Path(shutil.which('git')).resolve().parents[1] / 'bin/bash.exe'
    if git_bash.is_file():
        BASH = str(git_bash)
STUB = r'''#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$TEST_GH_CALLS"
case "$*" in
  'api --paginate '*)
    printf '%s\n' "$TEST_REFS"
    exit "${TEST_LIST_EXIT:-0}"
    ;;
  'api repos/example/repo/pulls/1 --jq .state')
    printf '%s\n' "${TEST_STATE:-closed}"
    ;;
  'api repos/example/repo/pulls/2 --jq .state')
    printf 'open\n'
    ;;
  'cache delete --all --ref refs/pull/1/merge --succeed-on-no-caches')
    exit "${TEST_DELETE_EXIT:-0}"
    ;;
  *) echo "Unexpected API call: $*" >&2; exit 99 ;;
esac
'''


class CacheCleanupContract(unittest.TestCase):
    def run_workflow(self, **extra):
        self.assertTrue(BASH, 'bash is required to execute the workflow contract')
        source = WORKFLOW.read_text(encoding='utf-8').split('        run: |\n', 1)[1]
        script = '\n'.join(line[10:] for line in source.splitlines()) + '\n'
        with tempfile.TemporaryDirectory(prefix='academy-cache-contract-') as tmp:
            root = Path(tmp)
            stub = root / 'gh'
            stub.write_text(STUB, encoding='utf-8', newline='\n')
            stub.chmod(0o700)
            target = root / 'workflow.sh'
            target.write_text(script, encoding='utf-8', newline='\n')
            calls = root / 'calls.txt'
            env = os.environ.copy()
            # Git Bash translates a Windows PATH inherited through the environment.
            env.update(PATH=str(root) + os.pathsep + env['PATH'], GH_REPO='example/repo',
                       GH_TOKEN='fixture-only', TEST_GH_CALLS=str(calls),
                       TEST_REFS='refs/heads/main\nrefs/pull/1/merge\nrefs/pull/2/merge')
            env.update(extra)
            result = subprocess.run([BASH, str(target)], env=env, capture_output=True,
                                    text=True, timeout=20)
            recorded = calls.read_text().splitlines() if calls.exists() else []
            self.assertTrue(recorded, f'The workflow did not reach the fake API: {result.stderr}')
            return result, recorded

    def test_only_closed_pr_cache_is_deleted(self):
        result, calls = self.run_workflow()
        self.assertEqual(result.returncode, 0, result.stderr)
        deletes = [x for x in calls if x.startswith('cache delete')]
        self.assertEqual(deletes, ['cache delete --all --ref refs/pull/1/merge --succeed-on-no-caches'])
        self.assertFalse(any('refs/heads/main' in x for x in deletes))

    def test_failed_partial_listing_deletes_nothing(self):
        result, calls = self.run_workflow(TEST_LIST_EXIT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)

    def test_unknown_pr_state_deletes_nothing(self):
        result, calls = self.run_workflow(TEST_STATE='unknown')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(x.startswith('cache delete') for x in calls))

    def test_invalid_refs_never_become_commands(self):
        result, calls = self.run_workflow(TEST_REFS='refs/heads/main\nrefs/pull/1/merge;exit 7\nrefs/pull/2/head\nrefs/pull/no/merge')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_deletion_error_is_visible(self):
        result, _ = self.run_workflow(TEST_DELETE_EXIT='1')
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
