#!/usr/bin/env python3
import re, sys
from collections import defaultdict

LOG_FILE  = "/home/opc/trading_bot/agent.log"
FROM_DATE = None
for arg in sys.argv[1:]:
    if arg.startswith("20"):
        FROM_DATE = arg
    else:
        LOG_FILE = arg

RE_OPEN  = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[OPEN\].*\[(.+?)\].*@ ([\d.]+).*USDC=([\d.]+).*SL=([\d.]+).*TP=([\d.]+)")
RE_CLOSE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[CLOSE\].*\[(.+?)\].*@ ([\d.]+).*PnL=([+-][\d.]+)%.*\(([+-][\d.]+) USDC\).*\| (.+)")
RE_INFO  = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[INFO\] (\d+) pos.*allocato=([\d.]+).*PnL=([+-][\d.]+)")

opens, trades, pnl_history = {}, [], []

try:
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
except FileNotFoundError:
    print("File non trovato: " + LOG_FILE)
    sys.exit(1)

for line in lines:
    if FROM_DATE and line[:10] < FROM_DATE[:10]:
        continue
    m = RE_OPEN.search(line)
    if m:
        ts, pair, price, usdc, sl, tp = m.groups()
        opens[pair] = {"open_time":ts,"entry":float(price),"usdc":float(usdc),"sl":float(sl),"tp":float(tp)}
        continue
    m = RE_CLOSE.search(line)
    if m:
        ts, pair, price, pnl_pct, pnl_usdc, reason = m.groups()
        entry = opens.pop(pair, {})
        trades.append({"pair":pair,"open":entry.get("open_time","?"),"close":ts,
            "entry":entry.get("entry",0),"exit":float(price),"usdc":entry.get("usdc",0),
            "pnl_pct":float(pnl_pct),"pnl_usdc":float(pnl_usdc),"reason":reason.strip()})
        continue
    m = RE_INFO.search(line)
    if m:
        ts, n_pos, allocated, pnl = m.groups()
        pnl_history.append((ts, float(pnl)))

print("\n" + "="*65)
print("  TRADING BOT - REPORT" + (" dal " + FROM_DATE if FROM_DATE else ""))
print("="*65)
print("\n" + "-"*65)
print("  TRADE CHIUSI (" + str(len(trades)) + " totali)")
print("-"*65)

if not trades:
    print("  Nessun trade chiuso.")
else:
    wins  = [t for t in trades if t["pnl_usdc"] > 0]
    losses= [t for t in trades if t["pnl_usdc"] <= 0]
    total = sum(t["pnl_usdc"] for t in trades)
    print("  " + "PAIR".ljust(15) + "ENTRY".rjust(10) + "EXIT".rjust(10) + "PnL%".rjust(7) + "PnL USDC".rjust(10) + "  REASON")
    print("  " + "-"*63)
    for t in trades:
        ok = "+" if t["pnl_usdc"] > 0 else "-"
        print("  " + ok + " " + t["pair"].ljust(13) + str(round(t["entry"],4)).rjust(10) +
              str(round(t["exit"],4)).rjust(10) + (str(round(t["pnl_pct"],1))+"%").rjust(7) +
              (str(round(t["pnl_usdc"],2))+"$").rjust(10) + "  " + t["reason"])
    print("\n  Totale: " + str(len(trades)) + " | Vincenti: " + str(len(wins)) +
          " (" + str(round(len(wins)/len(trades)*100)) + "%) | PnL: " + str(round(total,2)) + " USDC")
    if wins:   print("  Miglior trade: +" + str(round(max(t["pnl_usdc"] for t in wins),2)) + " USDC")
    if losses: print("  Peggior trade: " + str(round(min(t["pnl_usdc"] for t in losses),2)) + " USDC")

print("\n" + "-"*65)
print("  POSIZIONI APERTE (" + str(len(opens)) + ")")
print("-"*65)
if not opens:
    print("  Nessuna.")
else:
    for pair, p in opens.items():
        print("  " + pair.ljust(15) + str(round(p["entry"],4)).rjust(10) + str(round(p["usdc"],2)).rjust(8) +
              "$ SL=" + str(round(p["sl"],4)) + " TP=" + str(round(p["tp"],4)))

if pnl_history:
    print("\n" + "-"*65)
    print("  PnL ULTIMI 10 CICLI")
    print("-"*65)
    for ts, pnl in pnl_history[-10:]:
        print("  " + ts + "  " + str(round(pnl,2)) + " USDC")

print("\n" + "="*65 + "\n")
