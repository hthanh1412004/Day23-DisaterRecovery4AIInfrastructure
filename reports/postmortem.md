# Blameless postmortem — DR Drill Lab 23

## 1. Timeline

Timestamp dưới đây là UTC; ngày chạy 09/10/2026 giờ Việt Nam. Chỉ --auto trong drill, vận hành thật yêu cầu y/N.

| ISO time UTC | Sự kiện | Evidence |
|---|---|---|
| 2026-10-09T03:02:25.217578+00:00 | Outage A (netblock) | `chaos/chaos-events.jsonl:4` |
| 2026-10-09T03:02:26.915781+00:00 | Request đầu tiên lỗi | `reports/drill-2-withdr.jsonl:11` |
| 2026-10-09T03:02:40.130649+00:00 | Checker alert UNHEALTHY | `reports/health-events.jsonl:2` |
| 2026-10-09T03:02:48.195466+00:00 | Incident mở; --auto cho drill | `reports/runbook-run.jsonl:2` |
| 2026-10-09T03:02:49.464054+00:00 | Restore snapshot + RPO | `reports/failover-events.jsonl:5` |
| 2026-10-09T03:02:57.048950+00:00 | B ready | `reports/failover-events.jsonl:7` |
| 2026-10-09T03:02:58.568175+00:00 | Cutover | `reports/failover-events.jsonl:8` |
| 2026-10-09T03:03:02.944390+00:00 | Request đầu tiên thành công từ B | `reports/drill-2-withdr.jsonl:23` |

## 2. RTO/RPO và gap analysis

- RTO mục tiêu 300s; đo 37.7s; gap (đo − mục tiêu) = -262.30s. PASS, còn 262.30s headroom.
- RPO mục tiêu 300s; đo 14.02s / 7 doc; gap = -285.98s. PASS theo giây, cần ngân sách mất document riêng.
- Thành phần dài nhất: detection + xác nhận 24.227s, trong đó checker phát hiện sau 14.913s, runbook tiếp tục xác nhận độc lập. Tránh mô tả RTO chỉ là 15s + 6s + 5s vì còn timeout/HTTP overhead.
- Snapshot restore 0.020s; readiness/warm-up 7.585s; DNS/verify cuối 5.895s. Tổng đối chiếu `reports/rto-evidence.md`.
- Golden signals: 10 request trực tiếp B, p95 16.0 ms, error rate 0.0; `reports/runbook-run.jsonl:6`. Đây là kiểm tra smoke, không đủ đại diện SLO production hay latency qua edge.

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
| Consume alert đã threshold, giữ xác nhận operator | SRE | 12/10/2026 | Giảm phần xác nhận trùng, khoảng 9.3s trong run này |
| Standby full + weights/index preloaded | Platform | 13/10/2026 | Giảm đến khoảng 7.6s; chi phí compute cao hơn |
| Replication 5s + ingest watermark ngoài region | Data engineer | 14/10/2026 | Giảm cửa sổ RPO lý thuyết 30→5s; cần consistency/integrity gate |
| TTL 1s, reuse client và đo latency tại edge | Networking | 15/10/2026 | Giảm tối đa khoảng 4s cache; tăng control-plane/read frequency |

## 5. Câu hỏi bắt buộc và reflection

1. interval × threshold = 15.0s, khoảng 39.79% RTO. Đây là quy ước detect floor trong lab. Pha poll, timeout và probe tuần tự làm detection thực đo khác đôi chút.
2. Hạ interval 5→1s với threshold 3 giảm budget detection danh nghĩa 15→3s, tức 12s. Không cam kết RTO giảm đúng 12s: probe timeout 2s và overhead có thể vượt interval. Tăng probe load khoảng 5 lần; ba lỗi nhanh liên tiếp dễ cùng nằm trong transient burst, tăng flapping risk. Với 300s RTO, interval không thể dùng hết 100s (300/3) vì còn restore, warm-up và DNS; chọn 5s còn dư ngân sách trong drill.
3. Mất A vĩnh viễn trong 6 giờ: 7 doc là số write có ở primary mà snapshot phục hồi thiếu tại thời điểm restore; có thể là ticket đã được xác nhận nhận nhưng không tìm được. Không có nghĩa toàn bộ 6 giờ write bị mất. Cần replay từ durable queue/ingest ledger và thông báo phạm vi ảnh hưởng; phép đo local không chứng minh dữ liệu chịu được mất cả storage.
4. Trước implementation: không có detector; B count=0/weights=false; đổi pointer ngay sẽ region_not_ready. Process sống không đồng nghĩa ready.
5. Có thể giảm warm-up bằng pool full mà không giảm anti-flap threshold, đổi lại trả thêm compute. Có thể giảm TTL với chi phí refresh lớn hơn.
6. Checker chạy process riêng, không import serving. Nếu checker cùng process serving thì process chết sẽ mất cả detector; production nên đặt detector ngoài region.
7. Khi cần chứng minh RTO 5 phút, mở `reports/measure-drill-2.json`, rồi truy request và outage bằng bảng `reports/rto-evidence.md`. Không dùng số ví dụ từ GUIDE.
