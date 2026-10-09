# Runbook — Region A không phục vụ được

Owner incident: on-call SRE. Người quyết định failover/failback: incident commander (IC).
Chạy PowerShell tại thư mục repo. Dùng Python venv: thay python bằng .\.venv311\Scripts\python.exe trong các lệnh dưới nếu chưa activate venv.

| # | Bước | Lệnh copy-paste | Tín hiệu hoàn thành | Owner |
|---|---|---|---|---|
| 1 | Xác nhận outage | `python chaos/kill_region.py status --backend bare` | A không ready 3 lần liên tiếp; B alive; đối chiếu `reports/health-events.jsonl` | on-call |
| 2 | Mở incident + xác nhận | `python dr/runbook.py --primary a --target b --backend fs` | Log thong_bao_incident có ts và t_outage; nhập y khi IC đồng ý | on-call + IC |
| 3 | Restore state + scale GPU | Runbook ở bước 2 tự gọi failover đúng một lần | 2_restore_snapshot có RPO/docs_lost/version; 3_scale_pool ok | automation + Platform |
| 4 | Xác minh replica/readiness | `curl.exe http://127.0.0.1:8002/readyz` | HTTP 200, full, count >0, có weights; 4_wait_ready ok | automation + Data |
| 5 | Xác minh DNS cutover | `curl.exe http://127.0.0.1:8080/edge/state` | active_region=b sau TTL 5s; 5_dns_cutover sau ready | automation + Networking |
| 6 | Verify golden signals | `curl.exe http://127.0.0.1:8080/v1/infer` | region b; runbook 10 request trực tiếp B, error rate=0, p95 <1000ms; theo dõi traffic qua edge riêng | on-call |
| 7 | Đo RTO + postmortem | `python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | valid=true, warnings=[], PASS; ghi gap và evidence trong postmortem | SRE + IC |

Bước 3–6 tự thực hiện bên trong lệnh runbook ở bước 2. Không chạy snapshot get/failover lần thứ hai sau cutover: có thể ghi đè các write mới tại B.
Nếu chưa có snapshot, trước drill chạy `python state/replicate.py --every 30 --duration 150 --backend fs`; xác minh manifest state/_replica/dr-artifacts/MANIFEST.json. Vận hành thật phải có freshness, checksum và model/index compatibility gate; lab ghi embedding version và kiểm tra readiness, chưa có checksum gate.

Nếu readiness timeout, missing snapshot hoặc target chết: kết quả ok=false, không cutover. Giữ incident mở và sửa snapshot/target; không sửa pointer để bỏ qua gate. Nếu golden signals lỗi sau cutover, IC quyết định recovery; không auto failback.

**Rollback/failback:** chỉ khi A ổn định qua ít nhất 3 readiness probe cách nhau 5s, các write mới ở B đã backup và đồng bộ sang A, Data xác minh consistency/version, IC chấp thuận. `python chaos/kill_region.py restore --region a --backend bare` resume netblock, không đổi traffic; stop mode cần khởi động lại riêng A.
Trong lab: `python state/snapshot.py put --region b --backend fs` rồi `python dr/failover.py --target a --backend fs`. Failover restore snapshot B, scale A, đợi ready rồi cutover. Không gọi runbook outage đối với B đang healthy để ép failback. IC duyệt, SRE thực thi, Data kiểm tra. Giữ cooldown tối thiểu 60s và một IC; cooldown hiện là quy trình, chưa enforce bằng code.

**Tái lập:** `python scripts/lab.py core` tự seed lại state lab, chạy baseline 40s, ingest/replication 150s và drill 100s, lưu evidence, dừng process nó tạo. Chạy khi 8001/8002/8080 không bị dịch vụ khác chiếm. Tạo báo cáo: `python scripts/write_reports.py`.
Windows runner ghi PID interpreter thật; netblock dùng NtSuspendProcess/NtResumeProcess, stop kết thúc process lab. Serving, edge và công cụ chấm điểm được giữ nguyên.
