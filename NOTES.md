# Lab Day 04 - CP5 Analysis and Improvement

Name: Nguyen Van Minh
Student ID: 2A202603551

## 1. Case selected

- **Case ID:** E2 (Cloudflare CDN Cache Purge / FedRAMP scope)
- **Original score:** 100/100 (LLM judge)
- **Observed outcome:** Agent completed successfully and correctly described the incident and non-FedRAMP scope. However, the final answer did not contain the incident-detail URL returned by `get_incident_details`.
- **Original final answer (verbatim from `eval_before.json`):**

```text
Dựa trên dữ liệu trạng thái hệ thống của Cloudflare (`https://www.cloudflarestatus.com`) tại thời điểm snapshot (03:50:47 UTC, ngày 09/11/2026), đây là thông tin chi tiết về các incident liên quan đến **Cloudflare cache purge**:

### 1. Có incident nào liên quan không?
* **Có**, có một incident gần đây liên quan đến `CDN Cache Purge` đang ở trạng thái được ghi nhận (`identified`):
  * **Tên incident:** *"Fedramp Customers have Delayed Purges"* (ID: `wp49bp8bqo6c`, tạo ngày 06/11/2026).
  * **Mô tả từ Cloudflare:** Lỗi trong đường ống xử lý purge (purge pipeline) gây ra tình trạng chậm trễ purge cho khách hàng thuộc phạm vi FedRAMP, dẫn đến việc nội dung cũ (stale content) có thể vẫn được phục vụ cho đến khi hết hạn.
* Ngoài ra, trong 50 incident gần đây còn có một số incident lịch sử (đã `resolved`), ví dụ:
  * *"Cache purge delays in Qingdao Shandong, China"* (Đã giải quyết ngày 31/10/2026).
  * *"Cache Purging Delays Affecting Requests Routed Through EEUR"* (Đã giải quyết ngày 27/10/2026).

---

### 2. Một website CLB không thuộc FedRAMP có chắc bị ảnh hưởng không?
* **Không chắc chắn / Không bị ảnh hưởng theo thông báo chính thức:**
  * Trong phần cập nhật chi tiết của incident `wp49bp8bqo6c`, Cloudflare đã nêu rõ: *"Non-fedramp customers are not impacted"* (Khách hàng không thuộc FedRAMP không bị ảnh hưởng bởi sự cố này).
* **Giới hạn bằng chứng và lưu ý:**
  * Dữ liệu trạng thái hiện tại chỉ xác nhận sự cố `wp49bp8bqo6c` giới hạn phạm vi ở khách hàng FedRAMP.
  * Tuy nhiên, hệ thống trạng thái chỉ phản ánh các sự cố được hệ thống ghi nhận chính thức. Tool không đủ bằng chứng để loại trừ hoàn toàn các vấn đề cục bộ khác (nếu có) trên website của CLB. Do đó, không nên vội vàng quy kết mọi lỗi cache trên website CLB là do incident này gây ra khi chưa kiểm tra log, cấu hình hoặc trạng thái thực tế của website đó.
```

## 2. Trace evidence and root cause

The original trace contains the following successful incident lookup (condensed to the relevant fields):

```json
{
  "event": "tool",
  "step": 2,
  "name": "get_incident_details",
  "arguments": {
    "incident_id": "wp49bp8bqo6c",
    "provider": "cloudflare"
  },
  "result": {
    "ok": true,
    "id": "wp49bp8bqo6c",
    "url": "https://www.cloudflarestatus.com/incidents/wp49bp8bqo6c"
  }
}
```

The source URL was **https://www.cloudflarestatus.com/incidents/wp49bp8bqo6c**, but that exact URL was missing from the final answer. The answer referred to the general Cloudflare status website and to the incident ID. Thus this is a specific citation quality gap, not a claim that the whole response was incorrect.

- **Root cause:** The previous `run_agent()` returned `completed` immediately on model `finish='stop'`. It did not validate whether the answer included incident-specific source URLs already observed in successful tool results.
- **Judge limitation:** E2 scored 100 despite missing that incident-detail URL; automated judge scores do not establish that every evidence check passed.

## 3. Improvement implemented (CP5)

Changed **`agent.py`**, leaving fixtures, expected answers, grading code and provider adapters untouched.

1. Collect source URLs from successful `get_incident_details` results (`ok=True`, HTTP(S) URL).
2. Before accepting `finish='stop'`, check whether all these URLs appear in the answer.
3. For missing URLs, add a `validation_missing_source` event to the trace and ask the model to revise the answer using the verified URL(s).
4. The revision consumes another model step within the original `max_steps` budget; if the budget is exhausted, return `max_steps` rather than `completed`.
5. Failed incident lookups do not impose a URL requirement. No incident URL/ID is hard-coded.

**Why:** A final answer that already uses detailed incident evidence should include the corresponding incident-specific source, and the agent should verify that basic requirement before signaling completion.

**Offline validation:** `py -3.14 -m unittest discover -s tests -v` passed **39/39** tests after implementation, including the two previously failing `EvidenceGateTests`.

## 4. Before/after comparison (complete after CP6)

Same config required in both evaluations: `gemini` / `gemini-3.5-flash-lite` as agent and judge, `max_steps=8`, dataset `core`, snapshot inputs unchanged.

| Metric | `eval_before.json` | `eval_after.json` |
|---|---:|---:|
| Scored cases | 22/22 | Pending CP6 |
| Overall score / 100 | 94.77 | Pending CP6 |
| E2 score / 100 | 100 | Pending CP6 |
| Agent model calls (all cases) | 57 | Pending CP6 |
| Agent tokens (all cases) | 58674 | Pending CP6 |
| Judge tokens (all cases) | 43984 | Pending CP6 |
| E2 model calls | 3 | Pending CP6 |
| E2 agent tokens | 2755 | Pending CP6 |

**Improvement to check:** Missing incident-detail URLs should trigger a revision event and, when the budget permits, a corrected answer. E2 already scored 100, so the improvement should be evaluated using trace/source validation, not just the judge's numerical score.

**Known baseline weaknesses not claimed fixed:** H2 scored 50 due to missing required `get_status`; H7 scored 35 because agent accepted a user-requested false operational inference after a Cloudflare timeout. These require separate analysis and are not addressed by this single evidence-gate change.

**Actual CP6 result:** Pending. Do not claim improved score, token count or cost before generating and inspecting `eval_after.json`.

## 5. Limitations

- The LLM judge is approximate and can award a perfect score even when an incident URL is absent; judge and agent use the same model, which may introduce bias.
- Snapshot data from 2026-11-09 is historical, not evidence of the current live service state.
- One run before and one run after do not establish reproducibility or statistical reliability.
- Evidence Gate validates only URL presence, not that each individual factual claim is supported by that URL.
- Revisions use additional model calls/tokens and may increase quota consumption or exhaust `max_steps`.
- A case can still score poorly for missing tool calls or unsafe conclusions even when its incident URL is present.
