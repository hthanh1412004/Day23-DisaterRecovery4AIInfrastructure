# Bonus — Nguyễn Hữu Thành — 2A202602807

Cả sáu stretch goals trong GUIDE đã được xử lý. Các phép đo chạy localhost ngày 09/10/2026. Kết quả chính của bài lab vẫn ở `reports/rto-evidence.md`; bonus dùng log riêng.

## 1. Real MinIO

Docker daemon không chạy; link binary chính thức trả HTTP 410. Đã build MinIO từ source tag RELEASE.2025-09-07T16-13-09Z bằng Go 1.24.2 portable, sau đó chạy S3-compatible server thật tại 127.0.0.1:9000. Không thay bằng mock S3.
Bucket được tạo bằng boto3 trước snapshot. Đã thử 3 put/get mỗi backend, kiểm tra SHA256 SQLite và embedding version, rồi failover backend minio qua đầy đủ 5 bước và xác minh inference B qua edge.

| Backend | Mean put | Mean get | Số mẫu |
|---|---|---|---|
| fs | 0.010667s | 0.015333s | 3 |
| minio | 0.052333s | 0.067667s | 3 |

Evidence: `reports/bonus/minio-summary.json`, `reports/bonus/minio-failover.jsonl`.
Mẫu nhỏ, local disk và HTTP loopback; đây không phải benchmark cross-region/cloud. CLI MinIO sau build không chứa official build metadata đầy đủ, tag nguồn được pin trong hướng dẫn.
Tái lập: `python bonus/minio_drill.py` khi 8001/8002/8080/9000/9001 rảnh và đã có binary ở bonus/bin/minio.exe.
Nguồn [MinIO source](https://github.com/minio/minio/tree/RELEASE.2025-09-07T16-13-09Z).

## 2. Postgres PITR

PostgreSQL portable 17.11, cluster riêng trong bonus/data, chỉ listen 127.0.0.1:15432/15433; không sử dụng hay sửa database/service có sẵn.
Đã tạo metadata table, pg_basebackup với WAL stream, bật archive_command, ghi record keep trước recovery target, ghi discard sau target, pg_switch_wal và kiểm tra archive. Dừng primary; copy base backup, tạo recovery.signal, đặt recovery_target_time và restore_command, recovery_target_action=promote.
Phục hồi thật giữ **keep, seed**, loại **discard**, pg_is_in_recovery=false. RTO metadata layer = **3.741s**, restore_duration = 3.578s. Target = 2026-10-09 10:22:24.209578+07.
Evidence: `reports/bonus/pitr-events.jsonl`, `reports/bonus/pitr-summary.json`.
Metadata demo là layer riêng, chưa được nối vào serving API. RTO metadata không cộng trực tiếp vào RTO inference vì hai drill khác nhau.
Tái lập: `python bonus/pitr.py`; cần PostgreSQL runtime đầy đủ ở bonus/bin/pgsql. Windows restricted-token sandbox có thể chặn initdb, cần chạy bằng quyền người dùng thông thường.
Nguồn [PostgreSQL 17 PITR](https://www.postgresql.org/docs/17/continuous-archiving.html).

## 3. Active-active

Giữ A/B pool full, copy cùng state/weights, đợi readiness, chạy edge riêng bonus.active_active tại port 8081. 20 request thật: **A=10, B=10**, split 50/50. Khi upstream không serve được, proxy thử region còn lại; khi cả hai fail trả 503.
Conflict resolution: thứ tự toàn phần (logical_clock, region, body), giữ phiên bản lớn nhất. Idempotent cho cùng operation, tie-break deterministic region b > a. Test đủ 6 permutation của 3 update, cùng kết quả b-v2; test concurrent writes bằng 3 worker cũng b-v2.
Logical clock do client/producer cấp; production cần Lamport clock per writer, operation ID, durable outbox/replay và tombstone cho delete. Không dùng wall clock để né clock skew.
Merge ledger trong demo là SQLite tập trung; thiết kế hai ledger thực phải trao đổi cùng operation và chạy cùng hàm merge. Demo này chứng minh thuật toán và routing, chưa chứng minh distributed ledger HA hay convergence dưới partition.
Evidence: `reports/bonus/active-active.json`. Tái lập: `python bonus/verify_active_active.py`.

## 4. Terraform write-only

`bonus/terraform/replication.tf` khai báo primary us-east-1, replica us-west-2, bucket versioning cả hai, IAM role/policy cho S3 và aws_s3_bucket_replication_configuration. Terraform fmt -check và validate PASS, provider lock được lưu. Không chạy plan/apply, không tạo tài nguyên AWS.
put tương ứng upload vectors.sqlite/model.bin/MANIFEST.json; CRR copy object versions sang replica; get tương ứng đọc replica version đã chọn. Manifest chứa source, snapshot_at, latest_doc_ts, embedding version; versioning giữ lịch sử các object.
CRR không atomic trên ba object: production cần immutable snapshot prefix, checksum và object version IDs trong manifest; chỉ publish marker sau khi đủ dữ liệu được replicate. Versioning không tự đảm bảo manifest và weights/index cùng generation; cấu hình draft này không giải quyết transaction đó.
Nguồn [Terraform S3 replication resource](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_replication_configuration).

## 5. Randomized chaos — 5 runs

Random seed=23; mode và kill timing được chọn bởi PRNG, không sửa timestamps. Giữ interval=5s, threshold=3, warm-up=6s và edge TTL=5s. Chờ event checker thật rồi gọi runbook có xác nhận outage riêng, nên số đo có thêm khoảng xác nhận so với core drill.

| Run | Mode | Delay từ lúc launch traffic | RTO | Verdict | Traffic evidence |
|---|---|---|---|---|---|
| 1 | netblock | 11.355s | 48.9s | PASS | `reports/bonus/campaign-1791515512/chaos-1/traffic.jsonl` |
| 2 | stop | 6.102s | 51.8s | PASS | `reports/bonus/campaign-1791515512/chaos-2/traffic.jsonl` |
| 3 | netblock | 8.542s | 46.6s | PASS | `reports/bonus/campaign-1791515512/chaos-3/traffic.jsonl` |
| 4 | netblock | 6.782s | 48.9s | PASS | `reports/bonus/campaign-1791515512/chaos-4/traffic.jsonl` |
| 5 | stop | 7.604s | 51.8s | PASS | `reports/bonus/campaign-1791515512/chaos-5/traffic.jsonl` |

Mean RTO = **49.600s**; sample standard deviation (n−1) = **2.217s**.
Evidence summary: `reports/bonus/randomized-summary.json`; mỗi run có measure, health, failover, runbook và traffic riêng ở cùng thư mục campaign.
Không có ingest ở bonus timing này, RPO=0 là kết quả thiết kế; RPO nonzero được chứng minh ở core drill. stop trên Windows có thể biểu hiện ConnectTimeout thay ConnectError tức thì; không giả định signal mode quyết định duy nhất latency.
Thư mục reports/bonus/chaos-1 chứa lần thử 55s không đủ thời gian quan sát recovery; được giữ để audit nhưng không đưa vào thống kê. Chỉ campaign trong summary là 5 run hợp lệ.
Tái lập: `python scripts/lab.py randomized` (5 cửa sổ traffic 80s, tự dừng process đã tạo).

## 6. DR maturity self-assessment

Không có file slide/định nghĩa Level 0–4 trong repo. Dùng scale tự mô tả để tránh khẳng định sai rubric slide: Level 0 chưa DR; Level 1 backup; Level 2 restore/runbook có kiểm chứng; Level 3 detection + failover có readiness gate và drill định kỳ; Level 4 HA đa region liên tục, consistency/fencing và SLO chứng minh dưới nhiều failure mode.
Tự đánh giá hệ thống core **Level 3 trong phạm vi mô phỏng lab**: checker độc lập, anti-flap, failover gate, semi-auto confirm, measured RTO/RPO, audit logs. Không tự nhận Level 4 chỉ vì có demo active-active.
Để lên Level 4: detector/control-plane ngoài failure domain, fencing/single-flight/cooldown enforce bằng code; durable replicated ingest với conflict/tombstone, snapshot atomic/integrity checks; active-active state thật; concurrent traffic/end-to-end SLO; multi-node partition drills và restore định kỳ trong CI. Cần đối chiếu lại nhãn level với slide nếu giảng viên cung cấp taxonomy khác.
