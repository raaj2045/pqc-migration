import base64, json, os, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.expanduser("~/projects/research/pqc-migration/devnet/lib"))
import config
cfg=config.load(); BIN,CH,NODE,CID=cfg["PQCHAIND_BIN"],cfg["CHAIN_HOME"],cfg["CHAIN_NODE"],cfg["CHAIN_ID"]
MAX=4_194_304
def log(m): print(f"{time.strftime('%H:%M:%S')} {m}",flush=True)
def addr_of(k): return subprocess.run([BIN,"keys","show",k,"-a","--home",CH,"--keyring-backend","test"],capture_output=True,text=True).stdout.strip()
def sign(msgs,key,n):
    a=addr_of(key); m=json.loads(json.dumps(msgs[:n]))
    for x in m: x["signer"]=a
    tx={"body":{"messages":m,"memo":"","timeout_height":"0","extension_options":[],"non_critical_extension_options":[]},
        "auth_info":{"signer_infos":[],"fee":{"amount":[{"denom":"stake","amount":"6000"}],"gas_limit":str(600000+900000*n),"payer":"","granter":""}},"signatures":[]}
    f=tempfile.NamedTemporaryFile("w",suffix=".json",delete=False); json.dump(tx,f); f.close()
    s=subprocess.run([BIN,"tx","sign",f.name,"--from",key,"--chain-id",CID,"--keyring-backend","test","--home",CH,"--node",NODE,"--output-document","/dev/stdout"],capture_output=True,text=True)
    os.unlink(f.name)
    g=tempfile.NamedTemporaryFile("w",suffix=".json",delete=False); g.write(s.stdout); g.close()
    e=subprocess.run([BIN,"tx","encode",g.name,"--home",CH],capture_output=True,text=True)
    return g.name, len(base64.b64decode(e.stdout.strip()))
def bisect(msgs,key,tag):
    probes={}
    def sz(n):
        if n not in probes:
            p,raw=sign(msgs,key,n); os.unlink(p); probes[n]=raw
        return probes[n]
    lo,hi=1,len(msgs)
    if sz(hi)<=MAX:
        log(f"  {tag} all {hi} fit ({probes[hi]:,} B)"); return hi,probes
    while hi-lo>1:
        mid=(lo+hi)//2
        (lo,hi)=(mid,hi) if sz(mid)<=MAX else (lo,mid)
    log(f"  {tag} size ceiling {lo} ({probes[lo]:,} B); {hi} is {probes[hi]:,} B (over)")
    return lo,probes
def bcast(msgs,key,n,tag):
    p,raw=sign(msgs,key,n)
    b=subprocess.run([BIN,"tx","broadcast",p,"--node",NODE,"--home",CH,"-o","json"],capture_output=True,text=True,timeout=900)
    os.unlink(p)
    if b.returncode!=0:
        log(f"  {tag} N={n} raw={raw:,} -> REJECTED ({b.stderr.strip().splitlines()[-1][:80]})"); return False,raw,None
    d=json.loads(b.stdout)
    if d.get("code",0)!=0:
        log(f"  {tag} N={n} raw={raw:,} -> REJECTED CheckTx code={d['code']}"); return False,raw,None
    h=d["txhash"]; dl=time.time()+900
    while time.time()<dl:
        time.sleep(3)
        q=subprocess.run([BIN,"query","tx",h,"--node",NODE,"--home",CH,"-o","json"],capture_output=True,text=True)
        if q.returncode==0:
            r=json.loads(q.stdout); ok=r["code"]==0
            log(f"  {tag} N={n} raw={raw:,} -> {'ACCEPTED' if ok else 'FAILED code='+str(r['code'])} gas={int(r['gas_used']):,} h={r['height']}")
            return ok,raw,{"txhash":h,"gas_used":int(r["gas_used"]),"height":r["height"],"code":r["code"]}
    log(f"  {tag} N={n} -> not included in 900s"); return False,raw,None
out={}
for tag,path,key in [("secp256k1","/tmp/c_secp.json","validator"),("mldsa65","/tmp/c_ml.json","loadgen-pool-0")]:
    log(f"=== {tag} ===")
    msgs=json.load(open(path))
    c,probes=bisect(msgs,key,tag)
    over,_,_=bcast(msgs,key,c+1,tag)
    ok,raw,info=bcast(msgs,key,c,tag)
    out[tag]={"ceiling":c,"bytes_at_ceiling":probes[c],"bytes_above":probes.get(c+1),
              "above_rejected":not over,"at_accepted":ok,"tx":info,
              "probes":{str(k):v for k,v in sorted(probes.items())}}
json.dump(out,open("results/ceiling_4mb_confirmed.json","w"),indent=2)
a,b=out["secp256k1"]["ceiling"],out["mldsa65"]["ceiling"]
log(f"CEILINGS: secp256k1 {a}  ML-DSA-65 {b}  -> diff {a-b} packet(s), {(a-b)/a*100:.3f}%")
