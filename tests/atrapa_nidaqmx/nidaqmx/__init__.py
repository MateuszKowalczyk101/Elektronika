"""Atrapa nidaqmx: licznik zliczajacy losowe impulsy ~LOG['rate'] na sekunde."""
import random
import time

LOG = {"created": 0, "closed": 0, "chans": [], "terms": [], "fail_read_after": None, "rate": 200.0}


def reset():
    LOG.update(created=0, closed=0, chans=[], terms=[], fail_read_after=None, rate=200.0)


class _Channel:
    def __init__(self, name):
        self.name = name
        self._term = "/Dev1/PFI8"   # jak w prawdziwej karcie: domyslnie inne wejscie niz PFI0

    @property
    def ci_count_edges_term(self):
        return self._term

    @ci_count_edges_term.setter
    def ci_count_edges_term(self, value):
        self._term = value
        LOG["terms"].append(value)


class _CIChannels:
    def add_ci_count_edges_chan(self, counter, edge=None, initial_count=0):
        if counter not in ("Dev1/ctr0", "Dev1/ctr1"):
            raise RuntimeError(f"DaqError: Device identifier is invalid: {counter}")
        LOG["chans"].append((counter, edge, initial_count))
        return _Channel(counter)


class Task:
    def __init__(self):
        LOG["created"] += 1
        self.ci_channels = _CIChannels()
        self._count = 0
        self._reads = 0
        self._last = None

    def start(self):
        self._last = time.perf_counter()

    def read(self):
        self._reads += 1
        if LOG["fail_read_after"] is not None and self._reads > LOG["fail_read_after"]:
            raise RuntimeError("DaqError: device removed")
        now = time.perf_counter()
        expected = LOG["rate"] * (now - self._last)
        self._last = now
        whole = int(expected)
        self._count += whole + (1 if random.random() < expected - whole else 0)
        return self._count

    def stop(self):
        pass

    def close(self):
        LOG["closed"] += 1
