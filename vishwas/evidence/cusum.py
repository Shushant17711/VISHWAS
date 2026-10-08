"""Sequential change detection (CUSUM).

Instantaneous thresholding is the single most common mistake in anomaly
detection for physical systems.  Sensor noise, wind gusts and legitimate
manoeuvres all produce transient spikes, so a threshold tight enough to catch
a slow lie produces a false-accusation rate that is operationally catastrophic.

CUSUM instead accumulates evidence over time and tests for a *persistent
shift in the mean* of the residual stream.  That is exactly the right test for
the hardest attack class - slow trajectory drift - where the attacker moves
the lie away from truth by a small amount per timestep and never trips any
instantaneous threshold.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CusumState:
    statistic: float = 0.0
    alarmed: bool = False
    first_alarm_tick: int | None = None
    samples: int = 0
    last_z: float = 0.0

    def normalised(self, threshold: float) -> float:
        if threshold <= 0:
            return 0.0
        return min(1.0, self.statistic / threshold)


class CusumDetector:
    """One-sided CUSUM on a stream of standardised residuals.

    Parameters
    ----------
    k
        Slack, in sigma.  Deviations below this are treated as noise and do
        not accumulate.  Setting ``k`` to roughly half the smallest shift you
        care about is the textbook choice.
    h
        Alarm threshold on the accumulated statistic.  Higher ``h`` means
        longer detection latency but a lower false-alarm rate - this is the
        precision/latency trade-off curve we report rather than a single
        cherry-picked operating point.
    decay
        Mild forgetting factor so that very old evidence eventually ages out.
    """

    __slots__ = ("k", "h", "decay", "reset_on_alarm", "state")

    def __init__(
        self,
        k: float = 0.55,
        h: float = 6.5,
        decay: float = 0.995,
        reset_on_alarm: bool = False,
    ) -> None:
        self.k = k
        self.h = h
        self.decay = decay
        self.reset_on_alarm = reset_on_alarm
        self.state = CusumState()

    def update(self, z: float, tick: int) -> CusumState:
        """Feed one standardised residual and return the updated state."""
        st = self.state
        st.samples += 1
        st.last_z = z
        st.statistic = max(0.0, self.decay * st.statistic + (z - self.k))
        if st.statistic > self.h:
            if not st.alarmed:
                st.first_alarm_tick = tick
            st.alarmed = True
            if self.reset_on_alarm:
                st.statistic = 0.0
        elif st.statistic == 0.0:
            st.alarmed = False
        return st

    def abstain(self) -> CusumState:
        """No usable observation this tick - decay only, never accumulate."""
        self.state.statistic *= self.decay
        return self.state

    @property
    def score(self) -> float:
        """Accumulated statistic mapped to [0, 1] against the alarm level."""
        return self.state.normalised(self.h)

    def reset(self) -> None:
        self.state = CusumState()
