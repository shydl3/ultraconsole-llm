from __future__ import annotations

import time
from pathlib import Path

from .contracts import RunRequest
from .prompting import PROMPT_VERSION, prompt
from .results import aggregate_result, apply_answer, complete_json_answer, parse_answer
from .sampling import build_sample, windows
from .storage import VERSION, append_json, digest, iter_jsonl, progress, read_json, write_json

GENERATION_OPTIONS = {"do_sample": False, "repetition_penalty": 1.0, "no_repeat_ngram_size": 0, "max_time": 120}


class JsonAnswerStop:
    """Stop the single-answer request once its JSON object is complete."""

    def __init__(self, tokenizer, prompt_length):
        self.tokenizer = tokenizer
        self.prompt_length = prompt_length

    def __call__(self, input_ids, _scores, **_kwargs):
        text = self.tokenizer.decode(input_ids[0, self.prompt_length:], skip_special_tokens=True)
        return complete_json_answer(text)


class Qwen:
    def __init__(self, model_path: str):
        import torch
        import transformers
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        self.torch = torch
        torch.set_num_threads(8)
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
            raise RuntimeError("Qwen requires the verified CUDA/BF16 environment (VLM_PYTHON)")
        started = time.perf_counter()
        self.processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            model_path, dtype=torch.bfloat16, device_map={"": 0}, attn_implementation="sdpa", local_files_only=True).eval()
        self.model.generation_config.do_sample = False
        self.model.generation_config.temperature = 1.0
        self.model.generation_config.top_p = 1.0
        self.model.generation_config.top_k = 50
        self.runtime = {"torch": torch.__version__, "transformers": transformers.__version__,
                        "gpu": torch.cuda.get_device_name(), "dtype": str(next(self.model.parameters()).dtype),
                        "loadSeconds": time.perf_counter() - started, "promptVersion": PROMPT_VERSION,
                        "generationOptions": GENERATION_OPTIONS, "stopOnCompleteJson": True}

    def infer(self, frames, metadata, question, max_tokens):
        from transformers import StoppingCriteriaList

        torch = self.torch
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        messages = [{"role": "user", "content": [{"type": "video", "video": "sampled-video"}, {"type": "text", "text": question}]}]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.processor(text=[text], videos=[frames], video_metadata=[metadata],
                                do_sample_frames=False, do_resize=False, return_tensors="pt").to("cuda")
        stopping = StoppingCriteriaList([JsonAnswerStop(self.processor.tokenizer, inputs.input_ids.shape[1])])
        with torch.inference_mode():
            generated = self.model.generate(**inputs, max_new_tokens=max_tokens,
                                            stopping_criteria=stopping, **GENERATION_OPTIONS)
        result = generated[:, inputs.input_ids.shape[1]:]
        raw = self.processor.batch_decode(result, skip_special_tokens=True)[0]
        eos = self.model.generation_config.eos_token_id
        eos = eos if isinstance(eos, list) else [eos]
        json_complete = complete_json_answer(raw)
        finished = json_complete or (result.shape[1] > 0 and int(result[0, -1]) in eos)
        stats = {"inputTokens": inputs.input_ids.shape[1], "outputTokens": result.shape[1],
                 "inferenceSeconds": time.perf_counter() - started,
                 "peakAllocatedGiB": torch.cuda.max_memory_allocated() / 2**30,
                 "peakReservedGiB": torch.cuda.max_memory_reserved() / 2**30,
                 "generationTruncated": not finished,
                 "finishReason": "json_complete" if json_complete else "eos" if finished else "length_or_timeout"}
        del inputs, generated, result
        return raw, stats


def understand(run_dir: Path):
    config = read_json(run_dir / "request.json")
    request = RunRequest.model_validate(config["request"])
    candidates = list(iter_jsonl(run_dir / "candidates.jsonl"))
    events = {e.id: e for e in request.events if e.enabled}
    total = sum(len(c["eventDefinitionIds"]) for c in candidates)
    progress(run_dir, stage="understanding", totalRequests=total, completedRequests=0, failedRequests=0)
    if not total:
        write_json(run_dir / "summary.json", {"resultCount": 0, "failedRequests": 0, "reason": "no_candidates", "dryRun": request.dryRun})
        return
    model = None if request.dryRun else Qwen(config["modelPath"])
    if model:
        write_json(run_dir / "runtime.json", model.runtime)
    done, failed, counts = 0, 0, {"match": 0, "no_match": 0, "uncertain": 0}
    for candidate in candidates:
        for event_id in candidate["eventDefinitionIds"]:
            event = events[event_id]
            result_id = f"{candidate['candidateId']}-{event_id}"
            output = {"schemaVersion": "1", "runId": run_dir.name, "videoId": request.videoId, **candidate,
                      "eventDefinitionId": event_id, "description": event.description, "resultId": result_id,
                      "confidenceType": "model_self_reported_uncalibrated", "modelDecisionConfidence": None,
                      "inferenceStatus": "succeeded", "decision": "uncertain", "segments": []}
            try:
                for index, (start, end) in enumerate(windows(candidate["triggerSec"], event, config["video"]["durationSec"])):
                    sample_id = f"{result_id}-{index}"
                    frames, metadata, manifest = build_sample(run_dir, config, request, candidate, start, end, sample_id)
                    quality = manifest["quality"]
                    quality.update(preContextClipped=candidate["triggerSec"] - event.preSeconds < 0,
                                   postContextClipped=candidate["triggerSec"] + event.postSeconds > config["video"]["durationSec"])
                    write_json(run_dir / "samples" / f"{sample_id}.json", manifest)
                    part = {"window": manifest["window"], "quality": quality,
                            "sampleManifest": f"samples/{sample_id}.json", "previewPath": f"previews/{sample_id}.jpg"}
                    output["segments"].append(part)
                    if request.dryRun:
                        part.update(decision="uncertain", reason="仅抽帧预览，未调用模型", modelDecisionConfidence=None, downgradeReasons=[])
                    else:
                        question = prompt(candidate, event, manifest)
                        raw_dir = run_dir / "raw"
                        raw_dir.mkdir(exist_ok=True)
                        part["requestFingerprint"] = digest({"version": VERSION, "prompt": question, "manifest": manifest,
                                                             "model": config["modelPath"], "maxTokens": request.maxNewTokens,
                                                             "runtime": model.runtime, "video": config["video"]["sha256"]})
                        (raw_dir / f"{sample_id}.prompt.txt").write_text(question, encoding="utf-8")
                        last_error = None
                        attempts = []
                        for attempt in range(2):
                            (raw_dir / f"{sample_id}-{attempt}.prompt.txt").write_text(question, encoding="utf-8")
                            raw, stats = model.infer(frames, metadata, question, request.maxNewTokens)
                            raw_path = f"raw/{sample_id}-{attempt}.txt"
                            (run_dir / raw_path).write_text(raw, encoding="utf-8")
                            attempts.append({"rawOutputPath": raw_path, **stats})
                            try:
                                if stats["generationTruncated"] and not complete_json_answer(raw):
                                    raise ValueError("generation_truncated_or_timeout")
                                answer = parse_answer(raw, start, end, len(event.criteria or [event.description]), check_timestamps=False)
                                if not answer.videoDescription or not answer.videoDescription.strip():
                                    raise ValueError("missing_video_description")
                                apply_answer(part, answer)
                                part["rawOutputPath"] = raw_path
                                last_error = None
                                break
                            except (ValueError, TypeError) as exc:
                                last_error = exc
                                attempts[-1]["parseError"] = str(exc)
                                question = prompt(candidate, event, manifest, retry=True)
                        part["attempts"] = attempts
                        if last_error:
                            write_json(run_dir / "samples" / f"{sample_id}-failure.json", {"attempts": attempts, "error": str(last_error)})
                            raise ValueError(f"invalid_output: {last_error}")
                    del frames
                aggregate_result(output)
                if request.dryRun:
                    output["inferenceStatus"] = "dry_run"
                counts[output["decision"]] += 1
            except Exception as exc:
                failed += 1
                output.update(inferenceStatus="failed", decision="uncertain", error=f"{type(exc).__name__}: {exc}",
                              reason="推理或证据处理失败，不代表事件未发生。")
                append_json(run_dir / "errors.jsonl", {"resultId": result_id, "error": output["error"]})
                if model:
                    model.torch.cuda.empty_cache()
            append_json(run_dir / "results.jsonl", output)
            done += 1
            progress(run_dir, completedRequests=done, failedRequests=failed, decisions=counts)
    write_json(run_dir / "summary.json", {"resultCount": done, "failedRequests": failed, "decisions": counts,
                                          "dryRun": request.dryRun})
