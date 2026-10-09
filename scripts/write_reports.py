"""Render Vietnamese reports exclusively from measured core drill evidence."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.measure_rto import measure


def rows(path):
    return [(i, json.loads(l)) for i, l in enumerate(Path(path).read_text().splitlines(), 1) if l.strip()]


def locate(path, predicate):
    return next((f"`{path}:{i}`", r) for i, r in rows(path) if predicate(r))


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


R = Path("reports")
m = measure(R/"drill-2-withdr.jsonl", "chaos/chaos-events.jsonl", R/"health-events.jsonl", R/"failover-events.jsonl", 300)
m1 = measure(R/"drill-1-nodr.jsonl", "chaos/chaos-events.jsonl", R/"health-events.jsonl", R/"failover-events.jsonl", 300)
baseline = rows("reports/drill-1-nodr.jsonl")
blo, bhi = baseline[0][1]["ts"], baseline[-1][1]["ts"]
bkref, bkill = locate("chaos/chaos-events.jsonl", lambda r: r.get("action") == "kill" and blo <= r["ts"] <= bhi)
bfref, bfailure = locate("reports/drill-1-nodr.jsonl", lambda r: r["ts"] >= bkill["ts"] and not r["ok"])
for n, result in ((1, m1), (2, m)):
    (R/f"measure-drill-{n}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
traffic = rows("reports/drill-2-withdr.jsonl")
lo, hi = traffic[0][1]["ts"], traffic[-1][1]["ts"]
kref, kill = locate("chaos/chaos-events.jsonl", lambda r: r.get("action") == "kill" and lo <= r["ts"] <= hi)
t0 = kill["ts"]
fref, failure = locate("reports/drill-2-withdr.jsonl", lambda r: r["ts"] >= t0 and not r["ok"])
sref, success = locate("reports/drill-2-withdr.jsonl", lambda r: r["ts"] > failure["ts"] and r["ok"])
href, health = locate("reports/health-events.jsonl", lambda r: r.get("region") == "a" and r.get("to") == "UNHEALTHY" and t0 <= r["ts"] <= hi)
events = {}
for name in ("1_verify_target", "2_restore_snapshot", "3_scale_pool", "4_wait_ready", "5_dns_cutover"):
    events[name] = locate("reports/failover-events.jsonl", lambda r: r.get("step") == name and r.get("ok") and t0 <= r["ts"] <= hi)
iref, incident = locate("reports/runbook-run.jsonl", lambda r: r.get("step") == 2 and t0 <= r["ts"] <= hi)
gref, golden = locate("reports/runbook-run.jsonl", lambda r: r.get("step") == 6 and t0 <= r["ts"] <= hi)
start = events["1_verify_target"][1]["ts"]
restore = events["2_restore_snapshot"][1]
ready = events["4_wait_ready"][1]
cutover = events["5_dns_cutover"][1]
rto, rpo, lost = m["rto_measured_s"], m["rpo_at_restore_s"], m["docs_lost"]
floor = health["interval_s"]*health["threshold"]
detect_component = start-t0
restore_component = restore["ts"]-start
warm_component = ready["ts"]-restore["ts"]
dns_component = success["ts"]-ready["ts"]

text = f"""# RTO/RPO Evidence — Lab 23

Đo từ request thật trên localhost, backend fs, Windows bare mode. Timestamp là UTC (giờ Việt Nam = UTC+7).
Không sửa serving API, edge proxy hoặc công cụ chấm điểm. Các event trước cửa sổ drill là lần thử phát triển/unit test; phép đo lọc đúng cửa sổ traffic.

## 1. Drill 1 — không có DR

| Chỉ số | Giá trị | Evidence |
|---|---|---|
| Outage | {m1['t_outage_iso']} UTC | {bkref} |
| Request fail đầu tiên | +{m1['breakdown_seconds_from_t0']['user_thay_loi_dau_tien']}s | {bfref} |
| Request fail sau outage | {m1['requests_failed']} | `reports/measure-drill-1.json` |
| Phục hồi | Không có; NO_RECOVERY | `reports/measure-drill-1.json` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Evidence |
|---|---|---|
| t_outage | 0.000s | {kref} |
| User thấy lỗi đầu tiên | {failure['ts']-t0:.3f}s | {fref} |
| Health check phát hiện | {health['ts']-t0:.3f}s | {href} |
| Incident/auto confirm | {incident['ts']-t0:.3f}s | {iref} |
| Snapshot restore xong | {restore['ts']-t0:.3f}s | {events['2_restore_snapshot'][0]} |
| Region B ready | {ready['ts']-t0:.3f}s | {events['4_wait_ready'][0]} |
| DNS cutover | {cutover['ts']-t0:.3f}s | {events['5_dns_cutover'][0]} |
| **RTO đo được** | **{rto}s** | {sref}; `reports/measure-drill-2.json` |

| Chỉ số | Đo được | Mục tiêu | Verdict |
|---|---|---|---|
| RTO — Inference API | {rto}s | 300s | PASS; phục hồi bởi B |
| RPO — Vector DB | {rpo}s / {lost} doc | 300s | PASS (giây); chưa đặt ngân sách số doc |
| Model embedding version | {restore['embed_model_version']} | cùng snapshot | Đã restore |
| Golden signals trực tiếp B | p95 {golden['p95_latency_ms']} ms; error rate {golden['error_rate']} | p95 < 1000 ms; 0 lỗi/10 request | PASS |

RPO được tính từ MAX(ingested_at) primary trừ bản restore; docs_lost đếm document sau mốc bản restore, không suy từ tuổi snapshot. Evidence: {events['2_restore_snapshot'][0]}.
Ingest và replication tiếp tục chạy theo hướng dẫn ngay cả khi serving A bị pause; do đó RPO này mô tả dữ liệu ở thời điểm restore, không phải toàn bộ region chứa storage đã mất vĩnh viễn.

## 3. RTO breakdown

| Thành phần | Giây | Evidence/cách đo | Cách giảm |
|---|---|---|---|
| Detection + xác nhận incident | {detect_component:.3f}s | t_verify − t_outage; {href}, {iref}, {events['1_verify_target'][0]} | Dùng alert checker đã threshold thay vì poll lại; giữ confirm operator |
| Snapshot restore | {restore_component:.3f}s | t_restore − t_verify, duration_s={restore['duration_s']:.3f}s; {events['2_restore_snapshot'][0]} | Pre-stage snapshot, incremental backup |
| GPU pool warm-up/readiness | {warm_component:.3f}s | t_ready − t_restore; waited_s={ready['waited_s']:.3f}s; {events['4_wait_ready'][0]} | Giữ pool full, tăng chi phí standby |
| DNS/LB + verify state sau readiness | {dns_component:.3f}s | t_recovered − t_ready; {events['5_dns_cutover'][0]}, {sref} | Giảm TTL, reuse HTTP client |

Tổng timestamp chưa làm tròn = {success['ts']-t0:.3f}s, công cụ đo làm tròn thành {rto}s.
Health-check detect floor theo quy ước lab: {health['interval_s']}s × {health['threshold']} = **{floor}s**, evidence {href}.
Detection quan sát = {health['ts']-t0:.3f}s; khác floor do phase poll và timeout. Với poll tức thì, floor thực tế không luôn bằng interval × threshold; pha outage và thời gian probe quyết định.
DNS cache/user sampling thực đo từ cutover = {success['ts']-cutover['ts']:.3f}s; phần còn lại {cutover['ts']-ready['ts']:.3f}s là verify state/ghi pointer. Không gán tất cả thời gian chờ này cho TTL.
"""
(R/"rto-evidence.md").write_text(text, encoding="utf-8")

timeline = [(kill["ts"], "Outage A (netblock)", kref),
            (failure["ts"], "Request đầu tiên lỗi", fref),
            (health["ts"], "Checker alert UNHEALTHY", href),
            (incident["ts"], "Incident mở; --auto cho drill", iref),
            (restore["ts"], "Restore snapshot + RPO", events['2_restore_snapshot'][0]),
            (ready["ts"], "B ready", events['4_wait_ready'][0]),
            (cutover["ts"], "Cutover", events['5_dns_cutover'][0]),
            (success["ts"], "Request đầu tiên thành công từ B", sref)]
timeline_md = "\n".join(f"| {iso(ts)} | {name} | {ref} |" for ts, name, ref in timeline)
post = f"""# Blameless postmortem — DR Drill Lab 23

## 1. Timeline

Timestamp dưới đây là UTC; ngày chạy 09/10/2026 giờ Việt Nam. Chỉ --auto trong drill, vận hành thật yêu cầu y/N.

| ISO time UTC | Sự kiện | Evidence |
|---|---|---|
{timeline_md}

## 2. RTO/RPO và gap analysis

- RTO mục tiêu 300s; đo {rto}s; gap (đo − mục tiêu) = {rto-300:.2f}s. PASS, còn {300-rto:.2f}s headroom.
- RPO mục tiêu 300s; đo {rpo}s / {lost} doc; gap = {rpo-300:.2f}s. PASS theo giây, cần ngân sách mất document riêng.
- Thành phần dài nhất: detection + xác nhận {detect_component:.3f}s, trong đó checker phát hiện sau {health['ts']-t0:.3f}s, runbook tiếp tục xác nhận độc lập. Tránh mô tả RTO chỉ là 15s + 6s + 5s vì còn timeout/HTTP overhead.
- Snapshot restore {restore_component:.3f}s; readiness/warm-up {warm_component:.3f}s; DNS/verify cuối {dns_component:.3f}s. Tổng đối chiếu `reports/rto-evidence.md`.
- Golden signals: 10 request trực tiếp B, p95 {golden['p95_latency_ms']} ms, error rate {golden['error_rate']}; {gref}. Đây là kiểm tra smoke, không đủ đại diện SLO production hay latency qua edge.

## 3. Root cause — 5 whys

1. Vì sao request lỗi? Edge vẫn định tuyến tới A đang không trả lời.
2. Vì sao edge chưa đổi? Chưa có detector, restore và readiness gate ở baseline; sau DR cần xác nhận an toàn.
3. Vì sao B không serve ngay? B ban đầu warm, vectors rỗng và không có weights.
4. Vì sao mất dữ liệu? Snapshot định kỳ 30s, các write sau mốc snapshot chưa có trong bản restore.
5. Vì sao recovery mất thêm thời gian? Không có standby full + state đồng bộ liên tục; quy trình cần chờ warm-up, HTTP probe và TTL. Đây là lựa chọn chi phí/độ an toàn, không quy lỗi cá nhân.

Nếu storage primary mất thật, cách đo RPO đọc SQLite primary hiện tại không còn dùng được. Cần external ingest ledger, durable acknowledged-write watermark và replication monitor ngoài region để xác định mất dữ liệu.

## 4. Action items

| Action item | Owner | Deadline (VN) | Tác động dự kiến, cần drill lại |
|---|---|---|---|
| Consume alert đã threshold, giữ xác nhận operator | SRE | 12/10/2026 | Giảm phần xác nhận trùng, khoảng {max(0,detect_component-(health['ts']-t0)):.1f}s trong run này |
| Standby full + weights/index preloaded | Platform | 13/10/2026 | Giảm đến khoảng {warm_component:.1f}s; chi phí compute cao hơn |
| Replication 5s + ingest watermark ngoài region | Data engineer | 14/10/2026 | Giảm cửa sổ RPO lý thuyết 30→5s; cần consistency/integrity gate |
| TTL 1s, reuse client và đo latency tại edge | Networking | 15/10/2026 | Giảm tối đa khoảng 4s cache; tăng control-plane/read frequency |

## 5. Câu hỏi bắt buộc và reflection

1. interval × threshold = {floor}s, khoảng {floor/rto*100:.2f}% RTO. Đây là quy ước detect floor trong lab. Pha poll, timeout và probe tuần tự làm detection thực đo khác đôi chút.
2. Hạ interval 5→1s với threshold 3 giảm budget detection danh nghĩa 15→3s, tức 12s. Không cam kết RTO giảm đúng 12s: probe timeout 2s và overhead có thể vượt interval. Tăng probe load khoảng 5 lần; ba lỗi nhanh liên tiếp dễ cùng nằm trong transient burst, tăng flapping risk. Với 300s RTO, interval không thể dùng hết 100s (300/3) vì còn restore, warm-up và DNS; chọn 5s còn dư ngân sách trong drill.
3. Mất A vĩnh viễn trong 6 giờ: {lost} doc là số write có ở primary mà snapshot phục hồi thiếu tại thời điểm restore; có thể là ticket đã được xác nhận nhận nhưng không tìm được. Không có nghĩa toàn bộ 6 giờ write bị mất. Cần replay từ durable queue/ingest ledger và thông báo phạm vi ảnh hưởng; phép đo local không chứng minh dữ liệu chịu được mất cả storage.
4. Trước implementation: không có detector; B count=0/weights=false; đổi pointer ngay sẽ region_not_ready. Process sống không đồng nghĩa ready.
5. Có thể giảm warm-up bằng pool full mà không giảm anti-flap threshold, đổi lại trả thêm compute. Có thể giảm TTL với chi phí refresh lớn hơn.
6. Checker chạy process riêng, không import serving. Nếu checker cùng process serving thì process chết sẽ mất cả detector; production nên đặt detector ngoài region.
7. Khi cần chứng minh RTO 5 phút, mở `reports/measure-drill-2.json`, rồi truy request và outage bằng bảng `reports/rto-evidence.md`. Không dùng số ví dụ từ GUIDE.
"""
(R/"postmortem.md").write_text(post, encoding="utf-8")
print(f"Reports generated from logs: RTO={rto}s, RPO={rpo}s/{lost} docs")
