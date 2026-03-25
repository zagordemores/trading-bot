#!/usr/bin/env python3
"""
Multi-Pair Crypto Trading Agent v2 - Base DEX
Uniswap V3 + Aerodrome | 10 coppie | Flash Loan Arb | Sentiment Filter
Ottimizzato: OHLCV ogni 5 cicli, prezzi live batch (1 chiamata API)
"""

import os
import sys
import time
import threading
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config as cfg
from core.wallet    import create_wallet, load_wallet, load_wallet_from_env
from core.data      import fetch_all_pairs, fetch_current_prices_batch
from core.risk      import RiskManager
from core.sentiment import get_sentiment, SentimentZone
from core.arbitrage import ArbScanner, ArbExecutor
from dex.client     import DEXClient
from strategies.indicators import add_all_indicators
from strategies.strategy   import evaluate, Signal


def setup_logging(level: str = "INFO") -> None:
    fmt = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    stream_handler = logging.StreamHandler(sys.stdout)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    stream_handler.setFormatter(logging.Formatter(fmt))
    file_handler = logging.FileHandler("agent.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(fmt))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[stream_handler, file_handler],
    )

logger = logging.getLogger("agent")


class TradingAgentV2:
    def __init__(self, dry_run=None, enabled_pairs=None, arb_only=False):
        self.dry_run       = dry_run if dry_run is not None else cfg.AGENT["dry_run"]
        self.arb_only      = arb_only
        self.enabled_pairs = enabled_pairs or cfg.AGENT["enabled_pairs"]
        self.pairs_cfg     = {k: v for k, v in cfg.PAIRS.items() if k in self.enabled_pairs}

        logger.info(f"{'='*55}")
        logger.info(f"  Trading Agent v2 - Base DEX")
        logger.info(f"  Dry-run  : {self.dry_run}")
        logger.info(f"  Arb-only : {self.arb_only}")
        logger.info(f"  Coppie   : {len(self.pairs_cfg)}")
        logger.info(f"{'='*55}")

        self.account  = (load_wallet_from_env() if os.environ.get("PRIVATE_KEY")
                         else load_wallet())
        self.dex      = DEXClient(cfg.BASE_RPC_URL)
        self.risk     = RiskManager(cfg.RISK)
        self.scanner  = ArbScanner(self.dex)
        self.executor = ArbExecutor(self.dex.w3, self.account)

        self._sentiment         = None
        self._sentiment_tick    = 0
        self._SENTIMENT_REFRESH = 3
        self._last_report_day   = -1    # aggiorna sentiment ogni N cicli

        # Ottimizzazione API: OHLCV costoso, prezzi live economici (batch)
        self._ohlcv_tick        = 0
        self._OHLCV_REFRESH     = 20    # fetch OHLCV ogni N cicli (~5 min se ciclo=60s)
        self._cached_ohlcv      = {}   # cache dati storici

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        self._print_status()
        self._reconcile_positions()
        logger.info("[START] Agent avviato")

        # Thread arb separato — scansiona ogni 15 secondi
        def _arb_loop():
            import time as _time
            while True:
                try:
                    self._run_arb_scan()
                except Exception as e:
                    logger.warning(f'[ARB-THREAD] Errore: {e}')
                _time.sleep(15)
        arb_thread = threading.Thread(target=_arb_loop, daemon=True)
        arb_thread.start()
        logger.info('[ARB-THREAD] Loop arbitraggio avviato (ogni 15s)')

        # Thread liquidazioni — scansiona ogni 30 secondi
        from core.liquidation_monitor import LiquidationMonitor
        from core.reporter import send_telegram
        self.liq_monitor = LiquidationMonitor(self.dex.w3)

        def _liq_loop():
            import time as _time
            while True:
                try:
                    opps = self.liq_monitor.scan()
                    for opp in opps:
                        msg = self.liq_monitor.format_telegram_message(opp)
                        send_telegram(msg)
                        logger.info(f'[LIQ-THREAD] Notifica inviata: {opp.user[:10]}... profit=${opp.est_profit_usd:.2f}')
                except Exception as e:
                    logger.warning(f'[LIQ-THREAD] Errore: {e}')
                _time.sleep(30)
                # Report Telegram ogni 6 ore
                if not hasattr(_liq_loop, '_last_report'):
                    _liq_loop._last_report = 0
                if _time.time() - _liq_loop._last_report > 21600:
                    pre_alerts = [u for u in list(self.liq_monitor.watchlist)[:100] if True]
                    report = '*Report Liquidazioni*\nWatchlist: ' + str(len(self.liq_monitor.watchlist)) + ' utenti\nUltimo scan: ogni 30s\nNessuna liquidazione in corso'
                    send_telegram(report)
                    _liq_loop._last_report = _time.time()
                    logger.info('[LIQ-THREAD] Report 6h inviato')

        liq_thread = threading.Thread(target=_liq_loop, daemon=True)
        liq_thread.start()
        logger.info('[LIQ-THREAD] Loop liquidazioni avviato (ogni 30s)')

        while True:
            try:
                self._tick()
            except KeyboardInterrupt:
                logger.info("Agent fermato.")
                break
            except Exception as e:
                logger.error(f"Errore ciclo: {e}", exc_info=True)
            logger.info(f"[WAIT] Prossimo ciclo tra {cfg.AGENT['poll_interval_sec']}s\n")
            time.sleep(cfg.AGENT["poll_interval_sec"])

    def _tick(self) -> None:
        self.risk.tick_cooldowns()
        self._maybe_send_daily_report()

        # Aggiorna sentiment ogni N cicli
        if self._sentiment_tick % self._SENTIMENT_REFRESH == 0:
            try:
                self._sentiment = get_sentiment()
            except Exception as e:
                logger.warning(f"Sentiment non aggiornato: {e}")
        self._sentiment_tick += 1

        # ARB scan spostato in thread separato

        if self.arb_only:
            return

        # ── OHLCV: fetch solo ogni N cicli (dati storici cambiano lentamente) ─
        if self._ohlcv_tick % self._OHLCV_REFRESH == 0:
            logger.info(f"[FETCH] Fetch OHLCV per {len(self.pairs_cfg)} coppie...")
            self._cached_ohlcv = fetch_all_pairs(
                self.pairs_cfg,
                interval_minutes=cfg.STRATEGY["candle_interval"],
                lookback=cfg.STRATEGY["lookback_candles"],
            )
        else:
            logger.info(f"[CACHE] Uso OHLCV in cache (ciclo {self._ohlcv_tick % self._OHLCV_REFRESH}/{self._OHLCV_REFRESH})")
        self._ohlcv_tick += 1

        all_data = self._cached_ohlcv
        if not all_data:
            logger.warning("Nessun dato OHLCV disponibile, skip ciclo.")
            return

        # ── Prezzi live: UNA sola chiamata batch per tutte le coin ────────────
        coingecko_ids = [
            self.pairs_cfg[n]["coingecko_id"]
            for n in all_data.keys()
            if n in self.pairs_cfg
        ]
        live_prices = fetch_current_prices_batch(coingecko_ids)

        # Mappa coingecko_id -> pair_name
        id_to_pair = {v["coingecko_id"]: k for k, v in self.pairs_cfg.items()}

        # Sovrascrive ultima candela con prezzo live
        updated = 0
        for cg_id, price in live_prices.items():
            pair_name = id_to_pair.get(cg_id)
            if pair_name and pair_name in all_data:
                df = all_data[pair_name]
                if df is not None and not df.empty and price > 0:
                    all_data[pair_name].loc[df.index[-1], "close"] = price
                    updated += 1
        if updated:
            logger.info(f"[LIVE] Prezzi aggiornati per {updated} coppie (1 chiamata API)")

        balances     = self.dex.get_balances(self.account.address)
        usdc_balance = balances.get("USDC", 0.0)
        eth_balance  = balances.get("ETH",  0.0)
        logger.info(f"[BAL] USDC={usdc_balance:.2f} | ETH={eth_balance:.5f}")

        if not self.risk.has_enough_gas(eth_balance):
            logger.warning("Gas insufficiente.")
            return

        for pair_name, pair_cfg_item in self.pairs_cfg.items():
            df_raw = all_data.get(pair_name)
            if df_raw is None or len(df_raw) < 50:
                continue
            try:
                df     = add_all_indicators(df_raw, cfg.STRATEGY)
                signal = evaluate(df, cfg.STRATEGY, pair=pair_name)
                self._handle_signal(pair_name, pair_cfg_item, signal, balances, usdc_balance)
            except Exception as e:
                logger.error(f"[{pair_name}] Errore: {e}", exc_info=True)

        self._log_portfolio(all_data)

    # ── Flash Arb ─────────────────────────────────────────────────────────────

    def _run_arb_scan(self) -> None:
        logger.info("[SCAN] Scansione arbitraggio...")
        try:
            balances = self.dex.get_balances(self.account.address)
            usdc     = balances.get("USDC", 0.0)
        except Exception:
            usdc = 1000.0

        borrow     = min(5000.0, usdc * 0.10)
        borrow_map = {p: borrow for p in self.pairs_cfg}
        opps       = self.scanner.scan_opportunities(self.pairs_cfg, borrow_map)

        if not opps:
            logger.info("   Nessun arb profittevole.")
            return

        if (self._sentiment and
                self._sentiment.fear_greed_zone == SentimentZone.EXTREME_FEAR and
                self._sentiment.market_cap_change < -8.0):
            logger.warning("[WARN] Crash in corso - arb sospeso.")
            return

        for opp in opps[:2]:
            if not self.executor.is_ready() and not self.dry_run:
                logger.warning("FlashArb non deployato. Esegui --deploy-contract.")
                break
            self.executor.execute_arb(opp, self.pairs_cfg, dry_run=self.dry_run)
            time.sleep(1)

    # ── Trading direzionale ───────────────────────────────────────────────────

    def _handle_signal(self, pair_name, pair_cfg_item, signal, balances, usdc_balance):
        price = signal.price

        if pair_name in self.risk.positions:
            should_exit, reason = self.risk.check_exit(pair_name, price)
            if should_exit:
                self._close(pair_name, pair_cfg_item, balances, price, reason)
                return

        adj_conf = signal.confidence
        if self._sentiment:
            adj_conf *= self._sentiment.confidence_multiplier
            if signal.signal == Signal.BUY and not self._sentiment.is_buy_favorable:
                logger.info(f"[{pair_name}] BUY bloccato (sentiment: {self._sentiment.fear_greed_zone.value})")
                return
            if (self._sentiment.is_sell_favorable and
                    pair_name in self.risk.positions and
                    signal.signal != Signal.BUY):
                self._close(pair_name, pair_cfg_item, balances, price, "sentiment_sell")
                return

        if signal.signal == Signal.BUY and pair_name not in self.risk.positions:
            if adj_conf < cfg.STRATEGY["min_confidence"]:
                return
            can, reason = self.risk.can_open_new_position(pair_name, usdc_balance)
            if not can and not self.dry_run:
                logger.info(f"[{pair_name}] BUY bloccato: {reason}")
                return
            size_mult = getattr(signal, "size_mult", 1.0)
            rr_ratio  = getattr(signal, "rr_ratio",  3.0)
            size = self.risk.calc_trade_size(
                pair_name, pair_cfg_item, usdc_balance, adj_conf, size_mult)
            if size >= 1:
                self._open(pair_name, pair_cfg_item, size, price, rr_ratio)

        elif signal.signal == Signal.SELL and pair_name in self.risk.positions:
            self._close(pair_name, pair_cfg_item, balances, price, "segnale_sell")
        else:
            logger.info(
                f"[HOLD] [{pair_name}] HOLD | sig={signal.signal} conf={adj_conf:.0%} "
                f"| pos={'OK' if pair_name in self.risk.positions else '-'}"
            )

    def _open(self, pair_name, pcfg, usdc, price, rr_ratio=3.0):
        base, quote = pcfg["base_token"], pcfg["quote_token"]
        logger.info(f"[BUY] [{pair_name}] BUY {usdc:.2f} {quote} -> {base} @ {price:.6g} | R:R={rr_ratio:.0f}:1")
        if self.dry_run:
            self.risk.open_position(pair_name, base, quote, price, usdc, rr_ratio)
            return
        try:
            tx = self.dex.execute_swap(
                quote, base, usdc, self.account,
                pcfg["uni_fee_tier"], pcfg["preferred_dex"], cfg.RISK["max_slippage_pct"])
            logger.info(f"[{pair_name}] TX: https://basescan.org/tx/{tx}")
            self.risk.open_position(pair_name, base, quote, price, usdc, rr_ratio)
        except Exception as e:
            logger.error(f"[{pair_name}] BUY errore: {e}")

    def _close(self, pair_name, pcfg, balances, price, reason):
        base, quote   = pcfg["base_token"], pcfg["quote_token"]
        token_balance = balances.get(base, 0.0)
        logger.info(f"[SELL] [{pair_name}] SELL {token_balance:.6f} {base} @ {price:.6g} ({reason})")
        if self.dry_run or token_balance < 1e-8:
            self.risk.close_position(pair_name, price, reason)
            return
        try:
            tx = self.dex.execute_swap(
                base, quote, token_balance, self.account,
                pcfg["uni_fee_tier"], pcfg["preferred_dex"], cfg.RISK["max_slippage_pct"])
            logger.info(f"[{pair_name}] TX: https://basescan.org/tx/{tx}")
            self.risk.close_position(pair_name, price, reason)
        except Exception as e:
            logger.error(f"[{pair_name}] SELL errore: {e}")

    # ── Utility ───────────────────────────────────────────────────────────────

    def _log_portfolio(self, all_data: dict) -> None:
        prices = {
            n: float(df.iloc[-1]["close"])
            for n, df in all_data.items()
            if df is not None and len(df) > 0
        }
        s = self.risk.portfolio_summary(prices)
        logger.info(
            f"[INFO] {s['open_positions']} pos | "
            f"allocato={s['total_allocated']:.2f} USDC | "
            f"PnL={s['unrealized_pnl']:+.2f} USDC"
        )



    def _maybe_send_daily_report(self) -> None:
        now = datetime.now()
        if now.hour == 20 and now.day != self._last_report_day:
            self._last_report_day = now.day
            if not TELEGRAM_OK:
                return
            try:
                import re
                log_file = "agent.log"
                trades, wins = 0, 0
                total_pnl = 0.0
                pattern = re.compile(r"\[CLOSE\].*PnL=[+-][\d.]+%.*\(([+-][\d.]+) USDC\)")
                with open(log_file, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if datetime.now().strftime("%Y-%m-%d") in line:
                            m = pattern.search(line)
                            if m:
                                trades += 1
                                val = float(m.group(1))
                                total_pnl += val
                                if val > 0: wins += 1
                s = self.risk.portfolio_summary({})
                win_rate = (wins/trades*100) if trades > 0 else 0
                send_daily_report(s["open_positions"], s["total_allocated"],
                                  s["unrealized_pnl"], trades, win_rate, total_pnl)
            except Exception as e:
                logger.warning("Report giornaliero fallito: " + str(e))

    def _reconcile_positions(self) -> None:
        """
        Al riavvio, riconcilia le posizioni in memoria con i saldi reali del wallet.
        In dry-run usa i saldi simulati dal log. In live legge i saldi reali on-chain.
        """
        if self.dry_run:
            return  # in dry-run le posizioni vengono dal file positions.json

        logger.info("[RECONCILE] Riconciliazione posizioni con wallet reale...")
        try:
            balances = self.dex.get_balances(self.account.address)
        except Exception as e:
            logger.warning("[RECONCILE] Impossibile leggere saldi wallet: " + str(e))
            return

        for pair_name, pair_cfg_item in self.pairs_cfg.items():
            base  = pair_cfg_item["base_token"]
            quote = pair_cfg_item["quote_token"]
            balance = balances.get(base, 0.0)
            min_threshold = pair_cfg_item.get("min_trade_size", 1.0)

            # Se c'e saldo del token base ma non abbiamo la posizione in memoria
            if balance > 0.0001 and pair_name not in self.risk.positions:
                # Cerca l ultimo OPEN nel log per stimare il prezzo di entrata
                entry_price = self._find_last_entry_price(pair_name)
                if entry_price and entry_price > 0:
                    usdc_value = balance * entry_price
                    self.risk.open_position(pair_name, base, quote, entry_price, usdc_value)
                    logger.warning("[RECONCILE] Posizione " + pair_name + " ripristinata dal wallet @ " + str(round(entry_price, 4)))
                else:
                    logger.warning("[RECONCILE] " + pair_name + " ha saldo " + str(round(balance,6)) + " " + base + " ma nessun prezzo di entrata trovato nel log")

            # Se abbiamo la posizione in memoria ma saldo = 0 (chiusa fuori dal bot)
            elif balance < 0.0001 and pair_name in self.risk.positions:
                logger.warning("[RECONCILE] " + pair_name + " rimossa - saldo " + base + " = 0")
                self.risk.positions.pop(pair_name)
                self.risk._save_positions()

    def _find_last_entry_price(self, pair_name: str) -> float:
        """Cerca l ultimo prezzo di apertura per una coppia nel log."""
        import re
        log_file = "agent.log"
        pattern  = re.compile(r"\[OPEN\] \[" + re.escape(pair_name) + r"\] Aperta @ ([\d.]+)")
        last_price = 0.0
        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = pattern.search(line)
                    if m:
                        last_price = float(m.group(1))
        except Exception:
            pass
        return last_price

    def _print_status(self) -> None:
        try:
            b = self.dex.get_balances(self.account.address)
        except Exception:
            b = {}
        print(f"\n{'='*55}")
        print(f"  [WALLET] Wallet   : {self.account.address}")
        print(f"  ETH         : {b.get('ETH', 0):.6f}")
        print(f"  USDC        : {b.get('USDC', 0):.2f}")
        print(f"  Coppie      : {len(self.pairs_cfg)}")
        print(f"  Flash Arb   : {'[OK] pronto' if self.executor.is_ready() else '[WARN] deployal richiesto'}")
        print(f"  Dry-run     : {self.dry_run}")
        print(f"{'='*55}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Trading Agent v2 - Base DEX")
    parser.add_argument("--new-wallet",      action="store_true")
    parser.add_argument("--dry-run",         action="store_true")
    parser.add_argument("--arb-only",        action="store_true")
    parser.add_argument("--status",          action="store_true")
    parser.add_argument("--deploy-contract", action="store_true")
    parser.add_argument("--pairs",           nargs="+", default=None, metavar="PAIR")
    parser.add_argument("--log-level",       default=cfg.AGENT["log_level"],
                        choices=["DEBUG", "INFO", "WARNING"])
    args = parser.parse_args()

    setup_logging(args.log_level)

    if args.new_wallet:
        create_wallet()
        sys.exit(0)

    if args.deploy_contract:
        from contracts.deploy import deploy
        deploy()
        sys.exit(0)

    if args.pairs:
        invalid = [p for p in args.pairs if p not in cfg.PAIRS]
        if invalid:
            print(f"[ERR] Coppie non valide: {invalid}\nDisponibili: {list(cfg.PAIRS.keys())}")
            sys.exit(1)

    dry_run = args.dry_run or cfg.AGENT["dry_run"]
    agent   = TradingAgentV2(dry_run=dry_run, enabled_pairs=args.pairs, arb_only=args.arb_only)

    if args.status:
        agent._print_status()
        sys.exit(0)

    agent.run()


if __name__ == "__main__":
    main()
