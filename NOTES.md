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

## 4. Before/after comparison (CP6 completed)

Both evals used exactly the same configuration: `gemini` / `gemini-3.5-flash-lite` for both agent and judge, `max_steps=8`, `core` dataset, and the same status snapshot/scenarios. Both have **22/22 cases scored** and a non-null overall score.

| Metric | `eval_before.json` | `eval_after.json` | Difference |
|---|---:|---:|---:|
| Scored cases | 22/22 | 22/22 | 0 |
| Overall score / 100 | 94.77 | 95.91 | +1.14 |
| E2 score / 100 | 100 | 100 | +0 |
| E2 verified incident URL in answer | No | Yes | Fixed |
| Evidence-gate trace events | 0 | 9 | +9 |
| Agent model calls (all cases) | 57 | 62 | +5 |
| Agent tokens (all cases) | 58674 | 77386 | +18712 |
| Judge tokens (all cases) | 43984 | 42820 | -1164 |
| Total reported tokens | 102658 | 120206 | +17548 |
| E2 agent model calls | 3 | 4 | +1 |
| E2 agent tokens | 2755 | 4264 | +1509 |

### Evidence-gate confirmation in E2

- The before answer did **not** include the successful incident detail URL: `https://www.cloudflarestatus.com/incidents/wp49bp8bqo6c`.
- The after trace contains `validation_missing_source`; Gemini generated a revised answer including the exact URL above.
- E2 stayed at **100/100** in both runs. That is expected because the original judge had already awarded full points, despite the missing specific source link.
- Across all cases, the after trace contains **9 source-validation events**. These are direct mechanical evidence of the implemented behavior, independent of the judge score.

### Other score changes

- **H7:** 35 -> 60 points.

H7 increased, but its after trace contains no `validation_missing_source`. Therefore this score change **cannot be attributed to Evidence Gate** based on available evidence; model output or judge variability is a plausible explanation. H2 still scored **50** because it did not call the required `get_status` tool. H7 still gave an unsupported operational conclusion following a Cloudflare timeout and therefore remains a known weakness, despite the higher score.

### API usage and operational notes

The after run experienced temporary infrastructure failures: H1's judge returned HTTP 503, and H8's agent provider failed during a call. The resumable evaluator retried H1 **using its saved completed agent trace** and reran H8 with the original settings; ultimately both were scored. The final JSON files contain the full set of 22 case outcomes.

More agent calls and tokens were consumed after Evidence Gate (model calls 57 -> 62; agent tokens 58674 -> 77386). Judge token totals can vary between runs. USD cost cannot be determined from these files because no actual billing or per-token pricing data is recorded. The shown token sums reflect only successful, recorded usage, not necessarily failed or retried API requests.

## 5. Limitations

- The LLM judge is an approximate evaluator, not ground truth. It can award 100 even when an incident-specific source URL is missing. The agent and judge also use the same model, which may introduce bias.
- The 2026-11-09 status snapshot is historical. Its status and incidents should not be described as live service conditions.
- These are single before/after eval samples: a +1.14 change in mean score does not establish a statistically reliable or causal performance improvement.
- Evidence Gate validates only whether known tool URLs appear as exact substrings of the final answer, **not** whether each claim is accurate or supported by the cited URL.
- Revisions add model calls/tokens and can exhaust `max_steps` or increase use of free-tier quotas. A successful source revision does not imply better judge score.
- H2's missing status check and H7's unsupported conclusion after a Cloudflare timeout remain unaddressed; they are separate from the single CP5 Evidence Gate change.
- Aggregate token counts are model-reported and exclude unsuccessful requests for which no usage was recorded; monetary cost was not measured.
