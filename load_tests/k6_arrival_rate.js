// Offered load (constant-arrival-rate) per design.md. MODE: smoke | load | soak | isolation.
// Usage: k6 run -e MODE=load -e RATE=8500 -e DURATION=10m -e MAX_VUS=50000 -e BASE=http://host:8000 -e SUMMARY=out.json load_tests/k6_arrival_rate.js
// maxVUs is a capacity CEILING, not the load. Only run against systems you own.
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const MODE = __ENV.MODE || 'smoke';
const BASE = __ENV.BASE || 'http://127.0.0.1:8000';
const RATE = parseInt(__ENV.RATE || '50');
const MAX_VUS = parseInt(__ENV.MAX_VUS || '200');
const leaks = new Counter('cross_tenant_leaks');

const durations = { smoke: '1m', load: __ENV.DURATION || '10m', soak: __ENV.DURATION || '1h', isolation: '1m' };
const scenario = MODE === 'smoke'
  ? { executor: 'constant-vus', vus: Math.min(MAX_VUS, 10), duration: durations.smoke }
  : { executor: 'constant-arrival-rate', rate: MODE === 'isolation' ? 20 : RATE, timeUnit: '1s',
      duration: durations[MODE], preAllocatedVUs: Math.min(MAX_VUS, Math.max(50, Math.floor(RATE / 10))), maxVUs: MAX_VUS };

export const options = {
  scenarios: { main: scenario },
  thresholds: {
    http_req_duration: ['p(50)<240', 'p(95)<450', 'p(99)<590'],  // fast routes
    http_req_failed: ['rate<0.0005'],
    ...(MODE === 'isolation' ? { cross_tenant_leaks: ['count==0'] } : {}),
  },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

// Pre-minted per-tenant bearer tokens: TOKENS="tenantA:tokA,tenantB:tokB" (production-representative mode, AUTH_ENABLED=true)
const TOKENS = (__ENV.TOKENS || '').split(',').filter(Boolean).map(x => x.split(':'));
const MSGS = ['hello', 'hi there', 'thanks', 'what is the weather today'];  // fast routes: DIRECT_ANSWER / REJECT_OOS

export default function () {
  const headers = { 'Content-Type': 'application/json' };
  let tenant = null;
  if (TOKENS.length) { const t = TOKENS[__ITER % TOKENS.length]; tenant = t[0]; headers['Authorization'] = `Bearer ${t[1]}`; }
  const res = http.post(`${BASE}/v1/chat`, JSON.stringify({ message: MSGS[__ITER % MSGS.length] }), { headers });
  check(res, { 'status 200': r => r.status === 200 });
  if (MODE === 'isolation' && TOKENS.length > 1) {
    // explicit cross-tenant probe: tenant A token must not read tenant B resource (expects 404/403)
    const other = TOKENS[(__ITER + 1) % TOKENS.length];
    const r2 = http.get(`${BASE}/v1/operations/${other[0]}-op`, { headers });
    if (r2.status === 200) leaks.add(1);
  }
}

export function handleSummary(data) {
  const d = data.metrics.http_req_duration.values, f = data.metrics.http_req_failed.values, n = data.metrics.http_reqs.values;
  const out = { scenario: MODE, offered_rps: MODE === 'smoke' ? null : RATE, p50: d.med, p95: d['p(95)'], p99: d['p(99)'],
                error_rate: f.rate, rps: n.rate, requests: n.count, leaks: (data.metrics.cross_tenant_leaks || { values: { count: 0 } }).values.count };
  const files = {}; if (__ENV.SUMMARY) files[__ENV.SUMMARY] = JSON.stringify(out, null, 2);
  files.stdout = JSON.stringify(out, null, 2) + '\n';
  return files;
}
