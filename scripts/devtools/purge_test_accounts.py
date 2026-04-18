from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.live.helpers.test_accounts import collect_disposable_test_accounts, purge_test_accounts


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and purge disposable smoke-test accounts.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete the disposable test accounts instead of only reporting them.",
    )
    args = parser.parse_args()

    accounts = collect_disposable_test_accounts()
    unique_orgs = len({account.organization_id for account in accounts if account.organization_id})

    print(f"Disposable test accounts found: {len(accounts)}")
    print(f"Disposable test organizations found: {unique_orgs}")

    if not accounts:
        return 0

    preview = accounts[:20]
    print("Preview:")
    for account in preview:
        print(f"  {account.created_at}  {account.email}")
    if len(accounts) > len(preview):
        print(f"  ... {len(accounts) - len(preview)} more")

    if not args.apply:
        print("Dry run only. Re-run with --apply to purge these disposable accounts.")
        return 0

    summary = purge_test_accounts(accounts)
    print("Purge summary:")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
