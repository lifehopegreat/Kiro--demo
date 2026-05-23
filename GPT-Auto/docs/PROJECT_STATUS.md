# PROJECT_STATUS

## Current runtime snapshot
- Main entry: `C:/Users/???/Desktop/GPT-Auto/main.py`
- Config: `C:/Users/???/Desktop/GPT-Auto/config.json`
- Email provider mode: `outlook_manual`
- Current Roxy profile: `917b84a7db92824e9c8224a1947356fc`
- Current run step: `account_restart_loop`
- Current run email: `Artnme039@hunt91.xyz`
- Pending card exists: `True`

## File layout
- Runtime script: `main.py`
- Roxy API client: `roxy_client.py`
- Config: `config.json`
- Logs: `automation.log`
- Runtime state: `run_state.json`, `state.json`
- Card data: `cardmi.txt`, `used_cardmi.txt`, `cards_result.jsonl`, `manual_card_info.json`
- Email data: `outlook_accounts.txt`, `used_emails.txt`, `accounts.txt`
- Screenshots: `screenshots/`
- Docs: `docs/`
- Backups: `backups/`
- Helper scripts: `tools/`

## Quick counts
- cardmi.txt available lines: `0`
- used_cardmi.txt lines: `5`
- outlook_accounts.txt usable lines: `0`
- used_emails.txt lines: `33`

## Current findings
1. `cardmi.txt` is currently empty, so the next card-based run will fail unless manual card mode is enabled or new cardmi lines are added.
2. `run_state.json` still stores sensitive runtime artifacts (payment URLs, sms URLs, PayPal password, pending card details). Good for debugging, bad for long-term storage.
3. `main.py` is large (~90KB, ~79 top-level defs/classes). It works as a monolith now, but maintenance cost is high.
4. Helper/debug scripts have been moved into `tools/` to clean the root.
5. `config.json` contains live secrets in plain text (Roxy token, Gmail app password).

## Safe cleanup already done
- Moved `????.md` -> `docs/????.md`
- Moved `config.before_restore.json` -> `backups/config.before_restore.json`
- Moved `debug_imap.py`, `final_debug.py`, `test_imap.py`, `test_roxy.py` -> `tools/`

## Recommended next cleanup passes
- Pass A: create `runtime/` and redirect transient files (`run_state.json`, `session_debug.txt`, `card_debug.txt`) there.
- Pass B: split `main.py` into modules: config/state, email, cardmi, paypal, screenshots.
- Pass C: mask or prune sensitive values from `run_state.json` after each run.
