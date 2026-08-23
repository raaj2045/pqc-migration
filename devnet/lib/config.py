"""Config resolution for the devnet Python scripts.

Mirrors lib/config.js. Precedence, highest first:
  1. os.environ
  2. devnet.env (next to this package, if present)
  3. devnet.env.example defaults
  4. the generated env files inside DEVNET_DIR (ports.env, cosmos.env,
     deploy.env), which hold values produced by the devnet tooling itself
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_LINE = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$")


def _parse(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            if line.strip().startswith("#"):
                continue
            m = _LINE.match(line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def load():
    cfg = _parse(os.path.join(ROOT, "devnet.env.example"))
    cfg.update(_parse(os.path.join(ROOT, "devnet.env")))
    for k in list(cfg):
        if os.environ.get(k):
            cfg[k] = os.environ[k]

    devnet_dir = cfg.get("DEVNET_DIR", "")
    if not devnet_dir or not os.path.isdir(devnet_dir):
        raise SystemExit(f"DEVNET_DIR does not exist: {devnet_dir!r}")

    generated = {}
    for name in ("ports.env", "cosmos.env", "deploy.env"):
        generated.update(_parse(os.path.join(devnet_dir, name)))
    for k, v in generated.items():
        if not cfg.get(k):
            cfg[k] = v
    if not cfg.get("BEACON_URL") and generated.get("BEACON"):
        cfg["BEACON_URL"] = generated["BEACON"]

    return cfg


def require(cfg, *keys):
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        raise SystemExit(
            f"missing config: {', '.join(missing)} — set them in devnet.env, in the "
            f"environment, or make sure DEVNET_DIR ({cfg.get('DEVNET_DIR')}) has the "
            f"generated ports.env / cosmos.env / deploy.env"
        )
    return cfg


def path_in_devnet(cfg, name):
    return os.path.join(cfg["DEVNET_DIR"], name)
