#!/usr/bin/env python3
"""Confirm the max_tx_bytes (4 MB) ceiling by REAL broadcast, per signer key
type, now that the RPC max_body_bytes limit has been raised.

Runs end to end without pausing: geth keeps only ~128 blocks of historical
state, so once finality covers the sends there is a bounded window in which
the proof can still be fetched. Waiting for a human between steps can miss it.

Own broadcast+poll rather than sendtx.py: a ~700-packet tx burns ~99M gas and
can take longer to execute than sendtx.py's fixed 60s inclusion poll, which
would look like a failure when it is just a slow block.
"""
import base64, json, os, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.expanduser("~/projects/research/pqc-migration/devnet/lib"))
import config

cfg = config.load()
BIN, CH, NODE, CID = cfg["PQCHAIND_BIN"], cfg["CHAIN_HOME"], cfg["CHAIN_NODE"], cfg["CHAIN_ID"]
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_TX_BYTES = 4_194_304
SEND = sys.argv[1]

def log(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)
def addr_of(k): return subprocess.run([BIN,"keys","show",k,"-a","--home",CH,"--keyring-backend","test"],
                                      capture_output=True,text=True).stdout.strip()

def build(count, offset, out):
    r = subprocess.run(["node", os.path.join(HERE,"build-recv-msgs.js"), SEND,
                        f"--count={count}", f"--offset={offset}", f"--out={out}"],
                       capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"build failed: {r.stderr.strip().splitlines()[-1][:200]}")
    return json.loads(r.stdout.strip().splitlines()[-1])

def sign(msgs, key, gas):
    a = addr_of(key)
    m = json.loads(json.dumps(msgs))
    for x in m: x["signer"] = a
    tx = {"body":{"messages":m,"memo":"","timeout_height":"0","extension_options":[],"non_critical_extension_options":[]},
          "auth_info":{"signer_infos":[],"fee":{"amount":[{"denom":"stake","amount":"6000"}],"gas_limit":str(gas),"payer":"","granter":""}},
          "signatures":[]}
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump(tx,f); f.close()
    s = subprocess.run([BIN,"tx","sign",f.name,"--from",key,"--chain-id",CID,"--keyring-backend","test",
                        "--home",CH,"--node",NODE,"--output-document","/dev/stdout"],capture_output=True,text=True)
    os.unlink(f.name)
    if s.returncode != 0: raise RuntimeError("sign: "+s.stderr.strip().splitlines()[-1][:200])
    g = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); g.write(s.stdout); g.close()
    e = subprocess.run([BIN,"tx","encode",g.name,"--home",CH],capture_output=True,text=True)
    raw = len(base64.b64decode(e.stdout.strip())) if e.returncode == 0 else None
    return g.name, raw

def broadcast(signed_path, wait_s=600):
    b = subprocess.run([BIN,"tx","broadcast",signed_path,"--node",NODE,"--home",CH,"-o","json"],
                       capture_output=True,text=True,timeout=900)
    if b.returncode != 0:
        err = b.stderr.strip().splitlines()[-1] if b.stderr.strip() else b.stdout[-200:]
        return False, ("BODY_TOO_LARGE" if "too large" in b.stderr else
                       "TX_TOO_LARGE" if "tx too large" in (b.stderr+b.stdout).lower() else err[:200])
    d = json.loads(b.stdout)
    if d.get("code", 0) != 0:
        return False, f"CheckTx code={d['code']} {str(d.get('raw_log'))[:200]}"
    h = d["txhash"]
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(3)
        q = subprocess.run([BIN,"query","tx",h,"--node",NODE,"--home",CH,"-o","json"],capture_output=True,text=True)
        if q.returncode == 0:
            r = json.loads(q.stdout)
            return (r["code"] == 0), {"txhash":h,"code":r["code"],"height":r["height"],
                                      "gas_used":int(r["gas_used"]),"raw_log":str(r.get("raw_log"))[:200]}
    return False, f"not included within {wait_s}s (txhash {h})"

def trial(msgs, n, key, label):
    signed, raw = sign(msgs[:n], key, gas=600_000 + 900_000*n)
    b64 = (raw + 2)//3*4
    over = raw > MAX_TX_BYTES
    ok, info = broadcast(signed)
    os.unlink(signed)
    log(f"  {label} N={n:>4} raw={raw:>9,} b64={b64:>9,} {'(over max_tx_bytes)' if over else '':22} "
        f"-> {'ACCEPTED' if ok else 'REJECTED'} {info if not ok else 'gas='+format(info['gas_used'],',')+' h='+info['height']}")
    return ok, raw, info

# ---- wait for finality to cover the sends -------------------------------
send = json.load(open(SEND))
maxblk = max(p["blockNumber"] for p in send["packets"] if p["status"]=="committed")
beacon = cfg["BEACON_URL"].rstrip("/")
log(f"waiting for finality to cover block {maxblk}")
while True:
    d = json.loads(subprocess.run(["curl","-s","-m","20",f"{beacon}/eth/v1/beacon/light_client/finality_update"],
                                  capture_output=True,text=True).stdout)["data"]
    n = int(d["finalized_header"]["execution"]["block_number"])
    if n >= maxblk: break
    log(f"  finalized {n} / need {maxblk}")
    time.sleep(12)
log(f"finality covers {maxblk} (finalized {n}) — running immediately")

results = {}
# ---- secp256k1 ----------------------------------------------------------
log("=== secp256k1 (validator) ===")
build(700, 0, "/tmp/c_secp.json")
m = json.load(open("/tmp/c_secp.json"))
r = {}
ok700, raw700, _ = trial(m, 700, "validator", "secp")
r["700"] = {"accepted": ok700, "raw": raw700}
ok699, raw699, info699 = trial(m, 699, "validator", "secp")
r["699"] = {"accepted": ok699, "raw": raw699, "info": info699 if ok699 else str(info699)}
results["secp256k1"] = r

# ---- ML-DSA-65 ----------------------------------------------------------
log("=== ML-DSA-65 (loadgen-pool-0) ===")
build(700, 700, "/tmp/c_ml.json")
m2 = json.load(open("/tmp/c_ml.json"))
r2 = {}
okA, rawA, _ = trial(m2, 699, "loadgen-pool-0", "mldsa")
r2["699"] = {"accepted": okA, "raw": rawA}
okB, rawB, infoB = trial(m2, 698, "loadgen-pool-0", "mldsa")
r2["698"] = {"accepted": okB, "raw": rawB, "info": infoB if okB else str(infoB)}
results["mldsa65"] = r2

out = os.path.join(HERE, "results", "ceiling_4mb_confirmed.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump({"max_tx_bytes": MAX_TX_BYTES, "rpc_max_body_bytes": 16_000_000, "results": results},
          open(out,"w"), indent=2)
log(f"wrote {out}")
