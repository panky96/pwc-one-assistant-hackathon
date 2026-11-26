"""Telemetry: in-process counters and spans (workbench, KPI scripts) plus optional OpenTelemetry export.
Export is off unless OTEL_EXPORTER_OTLP_ENDPOINT or APPLICATIONINSIGHTS_CONNECTION_STRING is set.
Span attributes and metric labels never carry user text."""
from __future__ import annotations
import collections, contextlib, logging, os, statistics, threading, time
from collections import defaultdict

log = logging.getLogger("pwc.telemetry")
_lock = threading.Lock()
_durations: dict[str, list[float]] = defaultdict(list)
_counters: dict[str, float] = defaultdict(float)
_events: collections.deque = collections.deque(maxlen=500)
_T0 = time.perf_counter()
_MAX_SAMPLES = 10_000
_otel_tracer = None
_meter = None
_otel_counters: dict = {}
_otel_hist = None


def _attach(service_name: str) -> bool:
    global _otel_tracer, _meter, _otel_hist
    from opentelemetry import metrics, trace
    _otel_tracer = trace.get_tracer(service_name)
    _meter = metrics.get_meter(service_name)
    _otel_hist = _meter.create_histogram("pwc.span.duration", unit="ms")
    return True


def init_otel(endpoint: str | None, service_name: str, azure_connection_string: str | None = None) -> bool:
    conn = azure_connection_string or os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if conn:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
        except ImportError:
            log.warning("Azure export requested but azure-monitor-opentelemetry is not installed")
        else:
            configure_azure_monitor(connection_string=conn)
            return _attach(service_name)
    if not endpoint:
        return False
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("OTLP export requested but the OpenTelemetry SDK is not installed")
        return False
    res = Resource.create({"service.name": service_name})
    tp = TracerProvider(resource=res)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(tp)
    metrics.set_meter_provider(MeterProvider(resource=res, metric_readers=[
        PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))]))
    return _attach(service_name)


@contextlib.contextmanager
def span(name: str, **attrs):
    t0 = time.perf_counter()
    safe = {k: str(v) for k, v in attrs.items()}
    cm = _otel_tracer.start_as_current_span(name, attributes=safe) if _otel_tracer else contextlib.nullcontext()
    try:
        with cm:
            yield
    finally:
        dur = (time.perf_counter() - t0) * 1000
        with _lock:
            d = _durations[name]
            d.append(dur)
            if len(d) > _MAX_SAMPLES:
                del d[: _MAX_SAMPLES // 2]
            _events.append({"name": name, "start_ms": (t0 - _T0) * 1000, "end_ms": (t0 - _T0) * 1000 + dur,
                            "dur_ms": dur, "attrs": safe})
        if _otel_hist is not None:
            _otel_hist.record(dur, {"span": name})


def incr(name: str, v: float = 1.0) -> None:
    with _lock:
        _counters[name] += v
    if _meter is not None:
        c = _otel_counters.get(name)
        if c is None:
            c = _otel_counters[name] = _meter.create_counter(name)
        c.add(v)


def events(n: int = 300) -> list[dict]:
    with _lock:
        return list(_events)[-n:]


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
        _durations.clear(); _counters.clear(); _events.clear()


def snapshot() -> dict:
    return {"spans": {k: {"n": len(v), "p50": p(k, .5), "p95": p(k)} for k, v in _durations.items()}, "counters": dict(_counters)}
