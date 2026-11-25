"""Span recorder (OTel-compatible shape). Bridges to OpenTelemetry when the SDK and an endpoint are configured."""
from __future__ import annotations
import contextlib, statistics, threading, time
from collections import defaultdict

_lock = threading.Lock()
_durations: dict[str, list[float]] = defaultdict(list)
_counters: dict[str, float] = defaultdict(float)
_otel_tracer = None


def init_otel(endpoint: str | None, service_name: str) -> bool:
    global _otel_tracer
    if not endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError:
        return False
    prov = TracerProvider(resource=Resource.create({"service.name": service_name}))
    prov.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(prov)
    _otel_tracer = trace.get_tracer(service_name)
    return True


@contextlib.contextmanager
def span(name: str, **attrs):
    t0 = time.perf_counter()
    cm = _otel_tracer.start_as_current_span(name, attributes={k: str(v) for k, v in attrs.items()}) if _otel_tracer else contextlib.nullcontext()
    try:
        with cm:
            yield
    finally:
        with _lock:
            _durations[name].append((time.perf_counter() - t0) * 1000)


def incr(name: str, v: float = 1.0) -> None:
    with _lock:
        _counters[name] += v


def counter(name: str) -> float:
    return _counters.get(name, 0.0)


def p(name: str, q: float = 0.95) -> float | None:
    d = sorted(_durations.get(name, []))
    if not d:
        return None
    return d[min(len(d) - 1, int(q * len(d)))]


def mean(name: str) -> float | None:
    d = _durations.get(name)
    return statistics.fmean(d) if d else None


def count(name: str) -> int:
    return len(_durations.get(name, []))


def reset() -> None:
    with _lock:
        _durations.clear(); _counters.clear()


def snapshot() -> dict:
    return {"spans": {k: {"n": len(v), "p50": p(k, .5), "p95": p(k)} for k, v in _durations.items()}, "counters": dict(_counters)}
