import numpy as np, sys

W,H,STRIDE,UVOFF = 1344,760,1408,1081344

def stats(path):
    raw = np.fromfile(path, dtype=np.uint8)
    y = raw[:UVOFF].reshape(-1, STRIDE)[:H, :W].astype(np.float64)
    uv = raw[UVOFF:UVOFF + (H//2)*STRIDE].reshape(-1, STRIDE)
    u = uv[:H//2, 0:W:2].astype(np.float64)
    v = uv[:H//2, 1:W:2].astype(np.float64)
    # horizontal self-similarity: correlation between left third and middle third
    thirds = np.split(y[:, :W//3*3], 3, axis=1)
    def corr(a,b):
        a=a-a.mean(); b=b-b.mean()
        d=np.sqrt((a*a).sum()*(b*b).sum())
        return float((a*b).sum()/d) if d>0 else 0.0
    rep12, rep13 = corr(thirds[0],thirds[1]), corr(thirds[0],thirds[2])
    # bottom dead band: mean of last 10% rows vs overall
    bot = y[int(H*0.9):,:].mean()
    return dict(y_mean=y.mean(), y_std=y.std(), u_mean=u.mean(), v_mean=v.mean(),
                rep12=rep12, rep13=rep13, bottom_mean=bot)

for path in sys.argv[1:]:
    try:
        s = stats(path)
        print(f"{path}: y={s['y_mean']:.1f}±{s['y_std']:.1f} u={s['u_mean']:.1f} v={s['v_mean']:.1f} "
              f"3x-corr={s['rep12']:.2f}/{s['rep13']:.2f} bottom={s['bottom_mean']:.1f}")
    except Exception as e:
        print(f"{path}: ERR {e}")
