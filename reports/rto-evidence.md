# RTO/RPO Evidence — Lab 23

Đo từ request thật trên localhost, backend fs, Windows bare mode. Timestamp là UTC (giờ Việt Nam = UTC+7).
Không sửa serving API, edge proxy hoặc công cụ chấm điểm. Các event trước cửa sổ drill là lần thử phát triển/unit test; phép đo lọc đúng cửa sổ traffic.

## 1. Drill 1 — không có DR

| Chỉ số | Giá trị | Evidence |
|---|---|---|
| Outage | 2026-10-09T03:01:33 UTC | `chaos/chaos-events.jsonl:2` |
| Request fail đầu tiên | +2.4s | `reports/drill-1-nodr.jsonl:9` |
| Request fail sau outage | 10 | `reports/measure-drill-1.json` |
| Phục hồi | Không có; NO_RECOVERY | `reports/measure-drill-1.json` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Evidence |
|---|---|---|
| t_outage | 0.000s | `chaos/chaos-events.jsonl:4` |
| User thấy lỗi đầu tiên | 1.698s | `reports/drill-2-withdr.jsonl:11` |
| Health check phát hiện | 14.913s | `reports/health-events.jsonl:2` |
| Incident/auto confirm | 22.978s | `reports/runbook-run.jsonl:2` |
| Snapshot restore xong | 24.246s | `reports/failover-events.jsonl:5` |
| Region B ready | 31.831s | `reports/failover-events.jsonl:7` |
| DNS cutover | 33.351s | `reports/failover-events.jsonl:8` |
| **RTO đo được** | **37.7s** | `reports/drill-2-withdr.jsonl:23`; `reports/measure-drill-2.json` |

| Chỉ số | Đo được | Mục tiêu | Verdict |
|---|---|---|---|
| RTO — Inference API | 37.7s | 300s | PASS; phục hồi bởi B |
| RPO — Vector DB | 14.02s / 7 doc | 300s | PASS (giây); chưa đặt ngân sách số doc |
| Model embedding version | embed-model=vi-e5-base@v3 | cùng snapshot | Đã restore |
| Golden signals trực tiếp B | p95 16.0 ms; error rate 0.0 | p95 < 1000 ms; 0 lỗi/10 request | PASS |

RPO được tính từ MAX(ingested_at) primary trừ bản restore; docs_lost đếm document sau mốc bản restore, không suy từ tuổi snapshot. Evidence: `reports/failover-events.jsonl:5`.
Ingest và replication tiếp tục chạy theo hướng dẫn ngay cả khi serving A bị pause; do đó RPO này mô tả dữ liệu ở thời điểm restore, không phải toàn bộ region chứa storage đã mất vĩnh viễn.

## 3. RTO breakdown

| Thành phần | Giây | Evidence/cách đo | Cách giảm |
|---|---|---|---|
| Detection + xác nhận incident | 24.227s | t_verify − t_outage; `reports/health-events.jsonl:2`, `reports/runbook-run.jsonl:2`, `reports/failover-events.jsonl:4` | Dùng alert checker đã threshold thay vì poll lại; giữ confirm operator |
| Snapshot restore | 0.020s | t_restore − t_verify, duration_s=0.016s; `reports/failover-events.jsonl:5` | Pre-stage snapshot, incremental backup |
| GPU pool warm-up/readiness | 7.585s | t_ready − t_restore; waited_s=7.594s; `reports/failover-events.jsonl:7` | Giữ pool full, tăng chi phí standby |
| DNS/LB + verify state sau readiness | 5.895s | t_recovered − t_ready; `reports/failover-events.jsonl:8`, `reports/drill-2-withdr.jsonl:23` | Giảm TTL, reuse HTTP client |

Tổng timestamp chưa làm tròn = 37.727s, công cụ đo làm tròn thành 37.7s.
Health-check detect floor theo quy ước lab: 5.0s × 3 = **15.0s**, evidence `reports/health-events.jsonl:2`.
Detection quan sát = 14.913s; khác floor do phase poll và timeout. Với poll tức thì, floor thực tế không luôn bằng interval × threshold; pha outage và thời gian probe quyết định.
DNS cache/user sampling thực đo từ cutover = 4.376s; phần còn lại 1.519s là verify state/ghi pointer. Không gán tất cả thời gian chờ này cho TTL.
