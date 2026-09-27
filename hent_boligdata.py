"""
US Bolig Monitor - daglig datahentning.

Henter statsrenter, boliglaansrenter, arbejdsloeshedsansoegninger, boligpriser og
udbud fra FRED samt homebuilder-kurser fra Yahoo Finance, og skriver dem til
data/us_bolig_data.json (samme format som dashboardets database) og
data/us_bolig_daglig.csv (til Excel).

Kun Pythons standardbibliotek - intet skal installeres.
Koer:  py hent_boligdata.py   (eller: python hent_boligdata.py)
"""
import csv
import io
import json
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT_JSON = DATA / "us_bolig_data.json"
OUT_CSV = DATA / "us_bolig_daglig.csv"
LOG = ROOT / "log.txt"

WINDOW_DAYS = 400          # daglige/ugentlige serier
MONTHLY_START = "2024-06-01"  # nok til at beregne aar-til-aar for de seneste 13+ maaneder
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) US-Bolig-Monitor/1.0"}

FRED_DAILY = ["DGS10", "DGS30"]
FRED_WEEKLY = ["MORTGAGE30US", "MORTGAGE15US", "ICSA"]
FRED_MONTHLY = ["CSUSHPINSA", "HPIPONM226S", "ACTLISCOUUS", "PRIREDCOUUS"]
ETFS = ["ITB", "XHB"]
STOCKS = [("DHI", "D.R. Horton"), ("LEN", "Lennar"), ("PHM", "PulteGroup")]


def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            # FRED afviser browser-lignende User-Agents fra sky-IP'er; Yahoo kraever en.
            req = urllib.request.Request(url, headers={} if "fred.stlouisfed.org" in url else UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001 - netvaerksfejl af alle slags
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url} -> {last}")


def fred(series_id, start):
    """Returnerer {dato: vaerdi} fra FRED's offentlige CSV (kraever ingen API-noegle)."""
    txt = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}")
    out = {}
    rows = csv.reader(io.StringIO(txt))
    next(rows, None)
    for row in rows:
        if len(row) >= 2 and row[1] not in ("", "."):
            out[row[0]] = float(row[1])
    if not out:
        raise RuntimeError(f"{series_id}: ingen data")
    return out


def yahoo(ticker, rng):
    """Returnerer {dato: lukkekurs} fra Yahoo Finance."""
    last = None
    for host in ("query1", "query2"):
        try:
            txt = get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}?range={rng}&interval=1d")
            res = json.loads(txt)["chart"]["result"][0]
            closes = res["indicators"]["quote"][0]["close"]
            out = {}
            for ts, c in zip(res["timestamp"], closes):
                if c is not None:
                    out[datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()] = round(c, 2)
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"{ticker}: {last}")


def prev_year(m):
    y, mo = m.split("-")[:2]
    return f"{int(y) - 1}-{mo}"


def main():
    DATA.mkdir(exist_ok=True)
    old = {}
    if OUT_JSON.exists():
        try:
            old = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}

    start = (date.today() - timedelta(days=WINDOW_DAYS + 10)).isoformat()
    status, S = {}, {}

    for sid in FRED_DAILY + FRED_WEEKLY:
        try:
            S[sid] = fred(sid, start)
            status[sid] = f"ok ({max(S[sid])})"
        except Exception as e:  # noqa: BLE001
            status[sid] = f"FEJL: {e}"
    for sid in FRED_MONTHLY:
        try:
            S[sid] = fred(sid, MONTHLY_START)
            status[sid] = f"ok ({max(S[sid])[:7]})"
        except Exception as e:  # noqa: BLE001
            status[sid] = f"FEJL: {e}"
    for t in ETFS:
        try:
            S[t] = yahoo(t, "13mo")
            status[t] = f"ok ({max(S[t])})"
        except Exception as e:  # noqa: BLE001
            status[t] = f"FEJL: {e}"
    stocks = []
    for t, name in STOCKS:
        try:
            px = yahoo(t, "1y")
            ds = sorted(px)
            p, p1, p0 = px[ds[-1]], px[ds[-2]], px[ds[0]]
            stocks.append({"t": t, "n": name, "p": p, "d": round(p / p1 - 1, 4), "y": round(p / p0 - 1, 4), "asOf": ds[-1]})
            status[t] = f"ok ({ds[-1]})"
        except Exception as e:  # noqa: BLE001
            status[t] = f"FEJL: {e}"

    cutoff = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": status}

    # daily: renter + ETF'er, en raekke pr. handelsdag
    if all(k in S for k in ("DGS10", "DGS30", "ITB", "XHB")):
        days = sorted(d for d in set(S["DGS10"]) | set(S["ITB"]) if d >= cutoff)
        rows = []
        for d in days:
            r = {"d": d}
            if d in S["DGS10"]:
                r["u10"] = S["DGS10"][d]
            if d in S["DGS30"]:
                r["u30"] = S["DGS30"][d]
            if d in S["ITB"]:
                r["itb"] = S["ITB"][d]
            if d in S["XHB"]:
                r["xhb"] = S["XHB"][d]
            rows.append(r)
        out["daily"] = rows
    elif "daily" in old:
        out["daily"] = old["daily"]

    if "MORTGAGE30US" in S and "MORTGAGE15US" in S:
        out["weekly"] = [{"d": d, "m30": v, "m15": S["MORTGAGE15US"].get(d)}
                         for d, v in sorted(S["MORTGAGE30US"].items()) if d >= cutoff]
    elif "weekly" in old:
        out["weekly"] = old["weekly"]

    if "ICSA" in S:
        out["claims"] = [{"d": d, "v": round(v / 1000)} for d, v in sorted(S["ICSA"].items()) if d >= cutoff]
    elif "claims" in old:
        out["claims"] = old["claims"]

    if "CSUSHPINSA" in S and "HPIPONM226S" in S:
        cs = {d[:7]: v for d, v in S["CSUSHPINSA"].items()}
        fh = {d[:7]: v for d, v in S["HPIPONM226S"].items()}
        rows = []
        for m in sorted(cs):
            pm = prev_year(m)
            if pm in cs:
                r = {"m": m, "cs": round(cs[m] / cs[pm] - 1, 4)}
                if m in fh and pm in fh:
                    r["fhfa"] = round(fh[m] / fh[pm] - 1, 4)
                rows.append(r)
        out["prices"] = rows[-13:]
    elif "prices" in old:
        out["prices"] = old["prices"]

    if "ACTLISCOUUS" in S and "PRIREDCOUUS" in S:
        a = {d[:7]: v for d, v in S["ACTLISCOUUS"].items()}
        pr = {d[:7]: v for d, v in S["PRIREDCOUUS"].items()}
        rows = []
        for m in sorted(a):
            pm = prev_year(m)
            if pm in a:
                r = {"m": m, "active": round(a[m] / 1000), "yoy": round(a[m] / a[pm] - 1, 4)}
                if m in pr:
                    r["cut"] = round(pr[m] / a[m], 3)
                rows.append(r)
        out["supply"] = rows[-13:]
    elif "supply" in old:
        out["supply"] = old["supply"]

    out["stocks"] = stocks or old.get("stocks", [])

    tmp = OUT_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT_JSON)

    if out.get("daily"):
        with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["dato", "10-aarig", "30-aarig", "ITB", "XHB"])
            for r in out["daily"]:
                w.writerow([r["d"]] + [str(r.get(k, "")).replace(".", ",") for k in ("u10", "u30", "itb", "xhb")])

    fejl = [k for k, v in status.items() if v.startswith("FEJL")]
    log(("OK" if not fejl else f"DELVIS ({len(fejl)} fejl: {', '.join(fejl)})") + f" -> {OUT_JSON.name}")
    return 1 if len(fejl) == len(status) else 0


if __name__ == "__main__":
    sys.exit(main())
