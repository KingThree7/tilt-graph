"""Fold raw fills into positions: one position runs from flat to flat on one coin."""
from dataclasses import dataclass, asdict

EPS = 1e-9


@dataclass
class Position:
    id: str
    coin: str
    side: str            # "long" or "short"
    opened_at: int       # ms
    closed_at: int       # ms
    pnl: float           # closed pnl minus fees, in USDC
    fees: float
    max_notional: float  # largest size * price the position reached
    fills: int
    adds: int            # fills that grew the position after the first one
    taker_share: float   # share of fills that crossed the spread
    liquidated: bool = False  # the exchange closed it

    @property
    def minutes(self):
        return (self.closed_at - self.opened_at) / 60000

    def row(self):
        d = asdict(self)
        d["minutes"] = round(self.minutes, 2)
        d["won"] = self.pnl > 0
        d["ret"] = round(self.pnl / self.max_notional, 5) if self.max_notional else 0.0
        return d


def _signed(fill):
    sz = float(fill["sz"])
    return sz if fill["side"] == "B" else -sz


def build_positions(address, fills):
    """Return closed positions, oldest first. A position still open at the end is dropped."""
    open_by_coin, done = {}, []
    for f in fills:
        coin = f["coin"]
        if coin.startswith(("@", "#")) or "/" in coin:
            continue  # spot pairs and outcome contracts are not positions
        start = float(f["startPosition"])
        delta = _signed(f)
        end = start + delta
        px = float(f["px"])
        cur = open_by_coin.get(coin)

        if cur is None:
            if abs(start) > EPS:
                continue  # position was opened before our window: we cannot see its start
            cur = open_by_coin[coin] = dict(
                coin=coin, side="long" if delta > 0 else "short", opened_at=f["time"],
                pnl=0.0, fees=0.0, max_notional=0.0, fills=0, adds=0, takers=0, liq=False)
        grows = abs(end) > abs(start) + EPS and start * end >= 0
        if grows and cur["fills"] > 0:
            cur["adds"] += 1
        cur["fills"] += 1
        cur["takers"] += 1 if f.get("crossed") else 0
        cur["pnl"] += float(f.get("closedPnl") or 0)
        cur["liq"] = cur["liq"] or bool(f.get("liquidation"))
        cur["fees"] += float(f.get("fee") or 0)
        cur["max_notional"] = max(cur["max_notional"], abs(start) * px, abs(end) * px)

        crossed_zero = start * end < -EPS
        if abs(end) <= EPS or crossed_zero:
            done.append(Position(
                id=f"{address[:10]}-{coin}-{cur['opened_at']}", coin=coin, side=cur["side"],
                opened_at=cur["opened_at"], closed_at=f["time"],
                pnl=round(cur["pnl"] - cur["fees"], 4), fees=round(cur["fees"], 4),
                max_notional=round(cur["max_notional"], 2), fills=cur["fills"], adds=cur["adds"],
                taker_share=round(cur["takers"] / cur["fills"], 3), liquidated=cur["liq"]))
            del open_by_coin[coin]
            if crossed_zero:
                # one fill closed the old position and opened the opposite one
                open_by_coin[coin] = dict(
                    coin=coin, side="long" if end > 0 else "short", opened_at=f["time"],
                    pnl=0.0, fees=0.0, max_notional=abs(end) * px, fills=1, adds=0,
                    takers=1 if f.get("crossed") else 0, liq=False)
    done.sort(key=lambda p: (p.opened_at, p.id))
    return done
