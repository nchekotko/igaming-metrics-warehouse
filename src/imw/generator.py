"""Seeded simulator of an online operator (sportsbook + casino) — SYNTHETIC DEMO DATA.

The simulator walks every player day by day and emits events of 11 entity types into a landing
zone partitioned by *arrival* month (``landing/<entity>/arrival_month=YYYY-MM.jsonl``). Each
line is an envelope ``{"ingested_at", "source", "contract_version", "payload"}``.

Realistic defects are injected on purpose so the warehouse has something to catch:

* at-least-once delivery duplicates (same ``event_id`` delivered twice);
* late arrivals (hours to ~10 days) and a legacy casino provider delivering next-day batches;
* a legacy provider that sends local Europe/Malta wall-clock time without offset (DST!);
* wallet timestamps with non-UTC offsets (same instant, different representation);
* contract violations (missing fields, negative amounts, epoch timestamps, bad enums, broken JSON);
* heavy-tailed casino wins (Pareto tail with a 5000x max-win cap);
* ledger breaks: lost win callbacks and double credits in the wallet;
* attribution changes over time (late postbacks, affiliate disputes, fraud reversals).
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO
from zoneinfo import ZoneInfo

from imw.config import (
    AS_OF,
    EXPERIMENT_DAYS,
    EXPERIMENT_ID,
    EXPERIMENT_START,
    SYNTHETIC_LABEL,
    GeneratorConfig,
)

CONTRACT_VERSION = "1.3.0"
MALTA = ZoneInfo("Europe/Malta")
HELSINKI = ZoneInfo("Europe/Helsinki")

COUNTRIES = [
    ("DE", "EUR", 0.30),
    ("FI", "EUR", 0.12),
    ("IE", "EUR", 0.08),
    ("NL", "EUR", 0.10),
    ("GB", "GBP", 0.25),
    ("CA", "CAD", 0.15),
]
# Approximate EUR -> currency multipliers used only to size amounts in the simulator.
CCY_PER_EUR = {"EUR": 1.0, "GBP": 0.85, "CAD": 1.50}
CHANNELS = [("organic", 0.30), ("affiliate", 0.45), ("paid_search", 0.15), ("paid_social", 0.10)]
# affiliate_id: (volume weight, bonus-hunter share, value multiplier)
AFFILIATES = {
    "AFF01": (0.10, 0.02, 1.30),
    "AFF02": (0.12, 0.05, 1.00),
    "AFF03": (0.10, 0.05, 1.10),
    "AFF04": (0.10, 0.10, 0.90),
    "AFF05": (0.08, 0.10, 0.80),
    "AFF06": (0.28, 0.55, 0.60),
    "AFF07": (0.12, 0.08, 1.00),
    "AFF08": (0.10, 0.30, 0.70),
}


@dataclass(frozen=True)
class Segment:
    name: str
    p_ftd: float
    p_active: float
    churn: float
    dep_eur: float
    value: float


SEGMENTS = {
    "casual": Segment("casual", 0.55, 0.10, 0.030, 20, 1.0),
    "regular": Segment("regular", 0.80, 0.25, 0.010, 50, 1.0),
    "high_value": Segment("high_value", 0.95, 0.40, 0.006, 250, 1.0),
    "bonus_hunter": Segment("bonus_hunter", 0.92, 0.30, 0.080, 25, 1.0),
}

SPORTS = ["football", "tennis", "basketball", "ice_hockey", "esports"]
MARKETS = ["match_winner", "total_goals", "handicap", "both_teams_to_score"]
PROVIDERS = [("PRV_ALPHA", 0.50), ("PRV_BETA", 0.35), ("PRV_LEGACY", 0.15)]


def _pick(r: random.Random, items: list[tuple[Any, float]]) -> Any:
    x = r.random() * sum(w for _, w in items)
    for item, w in items:
        x -= w
        if x <= 0:
            return item
    return items[-1][0]


def _poisson(r: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= r.random()
        if p <= limit:
            return k
        k += 1


def iso_utc(ts: datetime) -> str:
    return ts.replace(microsecond=0).isoformat() + "Z"


def iso_zone(ts: datetime, zone: ZoneInfo) -> str:
    return ts.replace(tzinfo=UTC).astimezone(zone).replace(microsecond=0).isoformat()


def naive_local(ts: datetime, zone: ZoneInfo) -> str:
    """Local wall-clock time without offset — what the legacy provider sends."""
    return ts.replace(tzinfo=UTC).astimezone(zone).replace(microsecond=0, tzinfo=None).isoformat()


def assign_arm(player_id: str, experiment_id: str = EXPERIMENT_ID) -> str:
    h = hashlib.sha256(f"{experiment_id}:{player_id}".encode()).digest()
    return "treatment" if h[0] % 2 == 1 else "control"


def win_multiplier(r: random.Random) -> float:
    """Casino win multiplier: RTP ~0.956 with a heavy Pareto(alpha=1.9) tail capped at 2000x.

    70% of rounds lose, 29.7% pay an exponential multiplier (mean 2.79x), 0.3% hit the tail
    (x_m = 20, mean ~42x before the cap). alpha < 2 => infinite variance without the cap, which is
    what makes player-level NGR so noisy (see the experiment readout).
    """
    u = r.random()
    if u < 0.70:
        return 0.0
    if u < 0.997:
        return r.expovariate(1 / 2.79)
    return min(20.0 * r.paretovariate(1.9), 2000.0)


class LandingWriter:
    """Streams envelopes into arrival-month partitions and injects delivery defects."""

    def __init__(self, root: Path, cfg: GeneratorConfig, rng: random.Random):
        self.root = root
        self.cfg = cfg
        self.r = rng
        self.handles: dict[tuple[str, str], TextIO] = {}
        self.counts: dict[str, dict[str, int]] = {}

    def _write(self, entity: str, ingested_at: datetime, line: str) -> None:
        month = ingested_at.strftime("%Y-%m")
        key = (entity, month)
        if key not in self.handles:
            d = self.root / entity
            d.mkdir(parents=True, exist_ok=True)
            self.handles[key] = (d / f"arrival_month={month}.jsonl").open("w", encoding="utf-8", newline="\n")
        self.handles[key].write(line + "\n")

    def _count(self, entity: str, kind: str) -> None:
        c = self.counts.setdefault(entity, {"events": 0, "duplicates": 0, "late": 0, "invalid": 0})
        c[kind] += 1

    def _arrival(self, event_time: datetime, source: str) -> tuple[datetime, bool]:
        if source == "legacy-casino-gw":
            nxt = datetime.combine(event_time.date() + timedelta(days=1), datetime.min.time())
            return nxt + timedelta(hours=3, seconds=self.r.randint(0, 7200)), True
        if self.r.random() < self.cfg.late_rate:
            return event_time + timedelta(seconds=self.r.randint(3600, 10 * 86400)), True
        return event_time + timedelta(seconds=self.r.randint(1, 180)), False

    def emit(self, entity: str, payload: dict[str, Any], event_time: datetime, source: str) -> None:
        ingested_at, late = self._arrival(event_time, source)
        if ingested_at >= AS_OF:
            ingested_at = AS_OF - timedelta(seconds=self.r.randint(60, 3600))
        self._count(entity, "events")
        if late:
            self._count(entity, "late")
        body = payload
        if self.r.random() < self.cfg.invalid_rate:
            body = self._corrupt(dict(payload))
            self._count(entity, "invalid")
        line = self._envelope(ingested_at, source, body)
        if isinstance(body, str):  # broken JSON line
            line = body
        self._write(entity, ingested_at, line)
        if self.r.random() < self.cfg.duplicate_rate:
            dup_at = min(ingested_at + timedelta(seconds=self.r.randint(5, 3 * 86400)), AS_OF - timedelta(seconds=1))
            self._write(entity, dup_at, self._envelope(dup_at, source, body) if not isinstance(body, str) else body)
            self._count(entity, "duplicates")

    @staticmethod
    def _envelope(ingested_at: datetime, source: str, payload: Any) -> str:
        return json.dumps(
            {
                "ingested_at": iso_utc(ingested_at),
                "source": source,
                "contract_version": CONTRACT_VERSION,
                "payload": payload,
            },
            separators=(",", ":"),
        )

    def _corrupt(self, p: dict[str, Any]) -> Any:
        kind = self.r.randrange(7)
        keys = [k for k in p if k != "event_id"]
        if kind == 0 and keys:
            p.pop(self.r.choice(keys))
        elif kind == 1:
            for k in ("amount", "stake", "win"):
                if k in p:
                    p[k] = -abs(p[k] or 1.0)
                    break
            else:
                p["player_id"] = None
        elif kind == 2:
            if "currency" in p:
                p["currency"] = "XXX"
            else:
                p["player_id"] = "player-" + str(self.r.randint(1, 999))
        elif kind == 3:
            ts_keys = [k for k in p if k.endswith(("_ts", "_at")) and isinstance(p[k], str)]
            if ts_keys:
                p[ts_keys[0]] = int(datetime.fromisoformat(p[ts_keys[0]].replace("Z", "+00:00")).timestamp() * 1000)
        elif kind == 4:
            ts_keys = [k for k in p if k.endswith(("_ts", "_at")) and isinstance(p[k], str)]
            if ts_keys:
                p[ts_keys[0]] = p[ts_keys[0]].replace("Z", "")[:19]  # offset stripped
        elif kind == 5:
            for k in ("status", "tx_type", "event_type", "game_type", "channel", "arm"):
                if k in p:
                    p[k] = str(p[k]).upper()
                    break
        else:
            return '{"ingested_at":"' + iso_utc(datetime(2025, 1, 1)) + '","payload":{"event_id":'
        return p

    def close(self) -> None:
        for h in self.handles.values():
            h.close()


@dataclass
class Bonus:
    bonus_id: str
    bonus_type: str
    grant: float
    wr: float
    expires: datetime
    campaign_id: str | None
    wagered: float = 0.0


@dataclass
class PlayerState:
    pid: str
    currency: str
    seg: Segment
    value: float
    sb_share: float
    balance: float = 0.0
    dep_size: float = 0.0
    kyc_verified_at: datetime | None = None
    limit: float | None = None
    churned: bool = False
    excluded: bool = False
    bonuses: list[Bonus] = field(default_factory=list)
    seq: int = 0

    def next_id(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}-{self.pid}-{self.seq:06d}"


class Simulator:
    def __init__(self, cfg: GeneratorConfig, landing: Path):
        self.cfg = cfg
        self.out = LandingWriter(landing, cfg, random.Random(cfg.seed * 7919 + 1))
        self.end_ts = datetime.combine(cfg.end, datetime.min.time()) + timedelta(days=1)

    # ------------------------------------------------------------------ helpers
    def _money(self, x: float) -> float:
        return round(max(x, 0.01), 2)

    def wallet(
        self,
        st: PlayerState,
        ts: datetime,
        tx_type: str,
        amount: float,
        ref: str | None,
        method: str | None = None,
        r: random.Random | None = None,
    ) -> str:
        tx_id = st.next_id("TX")
        rep = iso_zone(ts, HELSINKI) if (r is not None and r.random() < 0.15) else iso_utc(ts)
        self.out.emit(
            "wallet_transactions",
            {
                "event_id": tx_id,
                "player_id": st.pid,
                "tx_ts": rep,
                "tx_type": tx_type,
                "amount": round(amount, 2),
                "currency": st.currency,
                "payment_method": method,
                "reference_id": ref,
            },
            ts,
            "wallet-service",
        )
        return tx_id

    def bonus_event(self, st: PlayerState, b: Bonus, ts: datetime, etype: str, amount: float) -> None:
        self.out.emit(
            "bonus_events",
            {
                "event_id": st.next_id("BE"),
                "bonus_id": b.bonus_id,
                "player_id": st.pid,
                "event_ts": iso_utc(ts),
                "event_type": etype,
                "bonus_type": b.bonus_type,
                "amount": round(amount, 2),
                "currency": st.currency,
                "wagering_requirement": round(b.wr, 2) if etype == "grant" else None,
                "campaign_id": b.campaign_id,
            },
            ts,
            "bonus-engine",
        )

    def rg_event(self, st: PlayerState, ts: datetime, etype: str, limit: float | None = None) -> None:
        self.out.emit(
            "rg_events",
            {
                "event_id": st.next_id("RG"),
                "player_id": st.pid,
                "event_ts": iso_utc(ts),
                "event_type": etype,
                "limit_amount": round(limit, 2) if limit else None,
                "limit_period": "day" if limit else None,
            },
            ts,
            "rg-service",
        )

    def grant_bonus(
        self, st: PlayerState, ts: datetime, btype: str, amount: float, wr_mult: float, campaign: str | None = None
    ) -> None:
        b = Bonus(st.next_id("BON"), btype, round(amount, 2), amount * wr_mult, ts + timedelta(days=30), campaign)
        st.bonuses.append(b)
        self.bonus_event(st, b, ts, "grant", b.grant)

    # ------------------------------------------------------------------ player
    def simulate_player(self, idx: int) -> None:
        cfg = self.cfg
        r = random.Random(cfg.seed * 1_000_003 + idx)
        pid = f"P{idx:06d}"
        n_days = (cfg.end - cfg.start).days + 1
        reg_day = cfg.start + timedelta(days=int(r.random() ** 0.8 * n_days))
        reg_ts = datetime.combine(reg_day, datetime.min.time()) + timedelta(seconds=r.randint(0, 86399))
        country, currency = _pick(r, [((c, cur), w) for c, cur, w in COUNTRIES])
        channel = _pick(r, CHANNELS)
        affiliate = _pick(r, [(a, v[0]) for a, v in AFFILIATES.items()]) if channel == "affiliate" else None
        hunter_share = AFFILIATES[affiliate][1] if affiliate else (0.06 if channel == "paid_social" else 0.02)
        value_mult = AFFILIATES[affiliate][2] if affiliate else 1.0
        u = r.random()
        if u < hunter_share:
            seg = SEGMENTS["bonus_hunter"]
        else:
            seg = _pick(r, [(SEGMENTS["casual"], 0.62), (SEGMENTS["regular"], 0.30), (SEGMENTS["high_value"], 0.08)])
        st = PlayerState(pid, currency, seg, value_mult, sb_share=r.betavariate(1.2, 1.2))
        ccy = CCY_PER_EUR[currency]
        st.dep_size = seg.dep_eur * value_mult * ccy * r.lognormvariate(0, 0.4)

        self.out.emit(
            "players",
            {
                "event_id": f"reg-{pid}",
                "player_id": pid,
                "registered_at": iso_utc(reg_ts),
                "country": country,
                "currency": currency,
                "birth_year": r.randint(1950, 2006),
                "marketing_consent": r.random() < 0.7,
            },
            reg_ts,
            "identity-service",
        )
        self._marketing(st, r, reg_ts, channel, affiliate)

        # KYC
        kyc_ts = reg_ts + timedelta(hours=r.uniform(0.1, 48))
        if kyc_ts < self.end_ts and r.random() < 0.9:
            doc = r.choice(["passport", "id_card", "driving_licence"])
            self._kyc(st, kyc_ts, "submitted", doc)
            done = kyc_ts + timedelta(hours=r.uniform(1, 120))
            u = r.random()
            if done < self.end_ts and u < 0.88:
                self._kyc(st, done, "verified", doc)
                st.kyc_verified_at = done
            elif done < self.end_ts and u < 0.92:
                self._kyc(st, done, "rejected", doc)

        if r.random() >= seg.p_ftd:
            return
        ftd_day = reg_day + timedelta(days=min(int(r.expovariate(0.7)), 20))
        if ftd_day > cfg.end:
            return
        self._activity(st, r, reg_ts, ftd_day, channel)

    def _kyc(self, st: PlayerState, ts: datetime, status: str, doc: str) -> None:
        self.out.emit(
            "kyc_events",
            {
                "event_id": st.next_id("KYC"),
                "player_id": st.pid,
                "event_ts": iso_utc(ts),
                "status": status,
                "document_type": doc,
            },
            ts,
            "kyc-service",
        )

    def _marketing(
        self, st: PlayerState, r: random.Random, reg_ts: datetime, channel: str, affiliate: str | None
    ) -> None:
        n_touch = r.randint(1, 3) if channel != "organic" else r.randint(0, 1)
        for i in range(n_touch):
            last = i == n_touch - 1
            ts = reg_ts - timedelta(minutes=r.randint(5, 60) if last else r.randint(60, 14 * 1440))
            ch = channel if last else _pick(r, CHANNELS)
            aff = affiliate if last else (r.choice(list(AFFILIATES)) if ch == "affiliate" else None)
            self.out.emit(
                "affiliate_touchpoints",
                {
                    "event_id": st.next_id("TP"),
                    "player_id": st.pid,
                    "touch_ts": iso_utc(ts),
                    "channel": ch,
                    "affiliate_id": aff,
                    "campaign": f"{ch}_{r.randint(1, 6):02d}" if ch != "organic" else None,
                },
                reg_ts,
                "tracking-service",
            )

        def attribute(ts: datetime, ch: str, aff: str | None, reason: str) -> None:
            if ts < self.end_ts:
                self.out.emit(
                    "attribution_events",
                    {
                        "event_id": st.next_id("ATT"),
                        "player_id": st.pid,
                        "attributed_at": iso_utc(ts),
                        "channel": ch,
                        "affiliate_id": aff,
                        "reason": reason,
                    },
                    ts,
                    "tracking-service",
                )

        attribute(reg_ts, channel, affiliate, "registration_last_click")
        u = r.random()
        if channel == "organic" and u < 0.06:
            aff = _pick(r, [(a, v[0]) for a, v in AFFILIATES.items()])
            attribute(reg_ts + timedelta(days=r.uniform(1, 20)), "affiliate", aff, "late_postback")
        elif channel == "affiliate" and u < 0.03:
            other = r.choice([a for a in AFFILIATES if a != affiliate])
            attribute(reg_ts + timedelta(days=r.uniform(10, 60)), "affiliate", other, "affiliate_dispute")
        elif channel == "affiliate" and st.seg.name == "bonus_hunter" and u < 0.20:
            attribute(reg_ts + timedelta(days=r.uniform(20, 90)), "organic", None, "fraud_reversal")

    # ------------------------------------------------------------------ activity loop
    def _activity(self, st: PlayerState, r: random.Random, reg_ts: datetime, ftd_day: date, channel: str) -> None:
        cfg = self.cfg
        exp_end = EXPERIMENT_START + timedelta(days=EXPERIMENT_DAYS)
        arm: str | None = None
        ftd_done = False
        d = ftd_day
        while d <= cfg.end and not st.excluded:
            day_start = datetime.combine(d, datetime.min.time())
            if d == EXPERIMENT_START and ftd_day < date(2025, 6, 1):
                arm = assign_arm(st.pid)
                at = day_start + timedelta(hours=6)
                self.out.emit(
                    "experiment_assignments",
                    {
                        "event_id": f"EXP-{EXPERIMENT_ID}-{st.pid}",
                        "player_id": st.pid,
                        "experiment_id": EXPERIMENT_ID,
                        "arm": arm,
                        "assigned_at": iso_utc(at),
                    },
                    at,
                    "crm-platform",
                )
                if arm == "treatment":
                    self.grant_bonus(
                        st, at, "crm_reactivation", 10 * CCY_PER_EUR[st.currency], 5, campaign=EXPERIMENT_ID
                    )
            in_exp = arm == "treatment" and EXPERIMENT_START <= d < exp_end
            if st.churned:
                if in_exp and r.random() < 0.004:
                    st.churned = False
                else:
                    d += timedelta(days=1)
                    self._expire_bonuses(st, day_start + timedelta(days=1))
                    continue
            elif ftd_done and r.random() < st.seg.churn:
                st.churned = True
                d += timedelta(days=1)
                continue
            p_active = st.seg.p_active * (1.12 if in_exp else 1.0)
            if not ftd_done or r.random() < p_active:
                t = day_start + timedelta(
                    hours=_pick(r, [(h, 1 + (h >= 17) * 2 + (h < 7) * -0.7) for h in range(24)]),
                    seconds=r.randint(0, 3599),
                )
                ftd_done = self._play_day(st, r, t, ftd_done, channel, in_exp) or ftd_done
            self._expire_bonuses(st, day_start + timedelta(days=1))
            d += timedelta(days=1)
        self._expire_bonuses(st, datetime.max, final=True)

    def _expire_bonuses(self, st: PlayerState, now: datetime, final: bool = False) -> None:
        keep = []
        for b in st.bonuses:
            if b.expires <= now or final:
                if b.expires < self.end_ts:
                    self.bonus_event(st, b, b.expires, "forfeit", b.grant)
                elif not final:
                    keep.append(b)
            else:
                keep.append(b)
        st.bonuses = keep

    def _deposit(self, st: PlayerState, r: random.Random, t: datetime, amount: float, deposited_today: float) -> float:
        if st.limit is not None and deposited_today + amount > st.limit:
            self.rg_event(st, t, "deposit_limit_hit", st.limit)
            amount = st.limit - deposited_today
        if amount < 5 * CCY_PER_EUR[st.currency]:
            return 0.0
        amount = round(amount, 2)
        method = _pick(r, [("card", 0.6), ("ewallet", 0.3), ("bank_transfer", 0.1)])
        tx = self.wallet(st, t, "deposit", amount, None, method, r)
        st.balance += amount
        p_cb = 0.03 if st.seg.name == "bonus_hunter" else 0.003
        if method == "card" and r.random() < p_cb:
            cb_ts = t + timedelta(days=r.uniform(10, 60))
            if cb_ts < self.end_ts:
                self.wallet(st, cb_ts, "chargeback", amount, tx, method, r)
                st.balance -= amount
        return amount

    def _play_day(
        self, st: PlayerState, r: random.Random, t: datetime, ftd_done: bool, channel: str, in_exp: bool
    ) -> bool:
        ccy = CCY_PER_EUR[st.currency]
        deposited = 0.0
        if not ftd_done or st.balance < 0.5 * st.dep_size or r.random() < (0.45 if in_exp else 0.3):
            want = st.dep_size * r.lognormvariate(0, 0.5)
            got = self._deposit(st, r, t, want, deposited)
            deposited += got
            if not ftd_done and got == 0:
                return False
            if not ftd_done:
                opt_in = {"affiliate": 0.6, "paid_search": 0.5, "paid_social": 0.6}.get(channel, 0.35)
                if st.seg.name == "bonus_hunter" or r.random() < opt_in:
                    self.grant_bonus(st, t + timedelta(seconds=2), "welcome", min(got, 100 * ccy), 10)
            elif not st.bonuses and st.seg.name in ("regular", "high_value") and r.random() < 0.03:
                self.grant_bonus(st, t + timedelta(seconds=2), "reload", 0.2 * got, 8)
            if ftd_done and r.random() < 0.3:  # second top-up in the same session
                deposited += self._deposit(
                    st, r, t + timedelta(minutes=20), st.dep_size * r.lognormvariate(0, 0.5), deposited
                )
        t += timedelta(seconds=30)
        staked = 0.0
        # sportsbook
        for _ in range(_poisson(r, 3.0 * st.sb_share)):
            stake = min(st.balance, st.dep_size * 0.15 * r.lognormvariate(0, 0.7))
            if stake < 0.5 * ccy:
                break
            staked += self._bet(st, r, t, round(stake, 2))
            t += timedelta(seconds=r.randint(20, 600))
        # casino session
        n_rounds = _poisson(r, 18.0 * (1 - st.sb_share))
        provider = _pick(r, PROVIDERS)
        game_type = _pick(r, [("slots", 0.8), ("live", 0.15), ("table", 0.05)])
        game_id = f"{provider}-{game_type[:2].upper()}{r.randint(1, 40):03d}"
        base_stake = max(0.1 * ccy, st.dep_size * 0.02 * r.lognormvariate(0, 0.3))
        for _ in range(n_rounds):
            stake = round(min(st.balance, base_stake), 2)
            if stake < 0.1 * ccy:
                break
            staked += stake
            self._round(st, r, t, provider, game_id, game_type, stake)
            t += timedelta(seconds=r.randint(8, 45))
        # bonus wagering progress / conversion
        for b in list(st.bonuses):
            if staked <= 0:
                break
            progress = min(staked, b.wr - b.wagered)
            if progress <= 0:
                continue
            b.wagered += progress
            self.bonus_event(st, b, t, "wager", progress)
            if b.wagered >= b.wr - 1e-9:
                hi = 1.1 if st.seg.name == "bonus_hunter" else 0.9
                conv = round(b.grant * r.uniform(0.2, hi), 2)
                self.bonus_event(st, b, t + timedelta(seconds=1), "convert", conv)
                self.wallet(st, t + timedelta(seconds=1), "bonus_convert", conv, b.bonus_id, None, r)
                st.balance += conv
                st.bonuses.remove(b)
        # withdrawal (KYC-verified players only)
        if (
            st.kyc_verified_at is not None
            and st.kyc_verified_at <= t
            and st.balance > 2 * st.dep_size
            and r.random() < 0.6
        ):
            amt = round(st.balance * r.uniform(0.5, 0.9), 2)
            self.wallet(
                st,
                t + timedelta(minutes=5),
                "withdrawal",
                amt,
                None,
                _pick(r, [("card", 0.3), ("ewallet", 0.4), ("bank_transfer", 0.3)]),
                r,
            )
            st.balance -= amt
        # responsible gaming
        if st.limit is None and r.random() < (0.012 if st.seg.name == "high_value" else 0.004):
            st.limit = round(st.dep_size * r.uniform(1.0, 2.0), 2)
            self.rg_event(st, t + timedelta(minutes=10), "deposit_limit_set", st.limit)
        if r.random() < 0.0008:
            self.rg_event(st, t + timedelta(minutes=15), "self_exclusion")
            st.excluded = True
        return True

    def _bet(self, st: PlayerState, r: random.Random, t: datetime, stake: float) -> float:
        bet_id = st.next_id("BET")
        n_legs = 1 if r.random() < 0.6 else r.randint(2, 5)
        legs = []
        for i in range(n_legs):
            odds = round(r.uniform(1.2, 3.5) if n_legs == 1 else r.uniform(1.2, 2.2), 2)
            # 2% void legs; otherwise the true win probability embeds a ~6% bookmaker margin
            res = "void" if r.random() < 0.02 else ("won" if r.random() < 1 / (odds * 1.06) else "lost")
            legs.append((i + 1, r.choice(SPORTS), r.choice(MARKETS), odds, res))
        total_odds = round(math.prod(o for *_, o, _ in legs), 2)
        horizon = r.uniform(0.5, 6) if n_legs == 1 else r.uniform(2, 96)
        settled = t + timedelta(hours=horizon)
        if any(res == "lost" for *_, res in legs):
            status, payout = "lost", 0.0
        elif all(res == "void" for *_, res in legs):
            status, payout = "void", stake
        else:
            eff = math.prod(o for *_, o, res in legs if res == "won")
            status, payout = "won", round(stake * eff, 2)
        st.balance -= stake
        self.wallet(st, t, "bet_stake", stake, bet_id, None, r)
        base = {
            "bet_id": bet_id,
            "player_id": st.pid,
            "placed_at": iso_utc(t),
            "bet_type": "single" if n_legs == 1 else "accumulator",
            "stake": stake,
            "total_odds": total_odds,
            "currency": st.currency,
        }
        self.out.emit(
            "bets",
            {
                "event_id": st.next_id("BEV"),
                **base,
                "event_ts": iso_utc(t),
                "settled_at": None,
                "status": "open",
                "payout": 0.0,
            },
            t,
            "sportsbook",
        )
        is_settled = settled < self.end_ts
        leg_ts = settled if is_settled else t
        for leg_no, sport, market, odds, res in legs:
            self.out.emit(
                "bet_legs",
                {
                    "event_id": st.next_id("LEG"),
                    "bet_id": bet_id,
                    "leg_no": leg_no,
                    "event_ts": iso_utc(leg_ts),
                    "sport": sport,
                    "market": market,
                    "odds": odds,
                    "leg_result": res if is_settled else "open",
                },
                leg_ts,
                "sportsbook",
            )
        if is_settled:
            self.out.emit(
                "bets",
                {
                    "event_id": st.next_id("BEV"),
                    **base,
                    "event_ts": iso_utc(settled),
                    "settled_at": iso_utc(settled),
                    "status": status,
                    "payout": payout,
                },
                settled,
                "sportsbook",
            )
            if payout > 0:
                self.wallet(st, settled, "bet_refund" if status == "void" else "bet_payout", payout, bet_id, None, r)
                st.balance += payout
        return stake

    def _round(
        self, st: PlayerState, r: random.Random, t: datetime, provider: str, game_id: str, game_type: str, stake: float
    ) -> None:
        round_id = st.next_id("RND")
        win = round(stake * win_multiplier(r), 2)
        legacy = provider == "PRV_LEGACY"
        self.out.emit(
            "game_rounds",
            {
                "event_id": round_id,
                "player_id": st.pid,
                "round_ts": naive_local(t, MALTA) if legacy else iso_utc(t),
                "provider_id": provider,
                "game_id": game_id,
                "game_type": game_type,
                "stake": stake,
                "win": win,
                "currency": st.currency,
            },
            t,
            "legacy-casino-gw" if legacy else "casino-aggregator",
        )
        st.balance -= stake
        self.wallet(st, t, "casino_stake", stake, round_id, None, r)
        if win > 0:
            st.balance += win
            if r.random() < self.cfg.lost_callback_rate:
                return  # win callback lost: round says win, wallet never credited
            self.wallet(st, t, "casino_win", win, round_id, None, r)
            if r.random() < self.cfg.double_credit_rate:
                self.wallet(st, t, "casino_win", win, round_id, None, r)  # double credit


def generate(landing: Path, cfg: GeneratorConfig | None = None) -> dict[str, Any]:
    """Generate the landing zone. Returns the manifest (also written to ``_manifest.json``)."""
    cfg = cfg or GeneratorConfig()
    landing.mkdir(parents=True, exist_ok=True)
    for old in landing.glob("*/arrival_month=*.jsonl"):
        old.unlink()
    sim = Simulator(cfg, landing)
    for idx in range(1, cfg.n_players + 1):
        sim.simulate_player(idx)
    sim.out.close()
    manifest = {
        "label": SYNTHETIC_LABEL,
        "seed": cfg.seed,
        "n_players": cfg.n_players,
        "period": [cfg.start.isoformat(), cfg.end.isoformat()],
        "contract_version": CONTRACT_VERSION,
        "injected": sim.out.counts,
    }
    (landing / "_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
