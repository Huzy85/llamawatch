"""llamawatch CLI entry point."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(prog="llamawatch", description="Self-hosted ops dashboard for local LLMs")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Auto-detect backends and write config")
    init_parser.add_argument("--guided", action="store_true", help="Interactive setup wizard")

    parser.add_argument("--port", type=int, help="Override port")
    parser.add_argument("--host", type=str, help="Override host")
    parser.add_argument("--no-auth", action="store_true", help="Disable auth for this session")

    args = parser.parse_args()

    if args.command == "init":
        from .auto_detect import run_init
        run_init(guided=args.guided)
        return

    cli_overrides = {}
    if args.port:
        cli_overrides["port"] = args.port
    if args.host:
        cli_overrides["host"] = args.host
    if args.no_auth:
        cli_overrides["auth_enabled"] = False

    from .config import load_config
    config = load_config(cli_overrides)

    host = config.get("host", "127.0.0.1")
    port = config.get("port", 8400)

    # Secure-by-default warning: exposing to the network with no password means
    # only the localhost-gated dangerous actions are protected; reads are open.
    loopback = host in ("127.0.0.1", "::1", "localhost")
    if not loopback and not config.get("auth_enabled"):
        print(
            "\n  ⚠  llamawatch is bound to a NETWORK address (" + str(host) + ") with NO password.\n"
            "     Anyone on your network can view the dashboard. Shell/Docker actions stay\n"
            "     localhost-only, but you should set a password: Settings → General → Authentication.\n",
            file=sys.stderr,
        )

    import uvicorn
    uvicorn.run("llamawatch.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
