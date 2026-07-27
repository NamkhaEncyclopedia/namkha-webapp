#!/usr/bin/env bash
# Run the app locally in test mode with dummy secrets. The app refuses to
# boot without NAMKHA_LOG_SALT and TURNSTILE_SECRET set; the Turnstile
# keys below are Cloudflare's published always-pass test pair, not real
# credentials.
#
# The sitekey has to be overridden too, not just the secret: the production
# sitekey is registered to the deployed hostnames, so on localhost the widget
# never issues a token and every submission is rejected as missing-token
# before the secret is ever used. The test sitekey works on any hostname.
set -euo pipefail

export NAMKHA_TEST_MODE=1
export NAMKHA_LOG_SALT="${NAMKHA_LOG_SALT:-dev}"
export TURNSTILE_SECRET="${TURNSTILE_SECRET:-1x0000000000000000000000000000000AA}"
export TURNSTILE_SITEKEY="${TURNSTILE_SITEKEY:-1x00000000000000000000AA}"

exec poetry run uvicorn app.main:app --reload
