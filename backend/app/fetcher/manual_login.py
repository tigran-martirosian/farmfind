"""CLI for saving Playwright auth state after manual login."""
from __future__ import annotations

import argparse

from .auth import save_manual_login


def main() -> None:
    parser = argparse.ArgumentParser(description="Save vendor auth state after manual login.")
    parser.add_argument("--vendor-id")
    parser.add_argument("--auth-state-label")
    parser.add_argument("--login-url")
    args = parser.parse_args()
    auth_state_label = args.auth_state_label
    login_url = args.login_url
    if args.vendor_id:
        from .account_connections import connection_config_or_default

        config = connection_config_or_default(args.vendor_id)
        auth_state_label = auth_state_label or config.auth_state_label
        login_url = login_url or config.login_url
    if not auth_state_label or not login_url:
        parser.error("Provide --vendor-id or both --auth-state-label and --login-url.")
    path = save_manual_login(auth_state_label, login_url)
    print(f"Saved auth state to {path}")


if __name__ == "__main__":
    main()
