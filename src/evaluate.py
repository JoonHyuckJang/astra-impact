"""정답 라벨과 비교해 정확도를 잰다.

비교 대상
  AI        results/C*.json (impact_analyzer.py offline 결과)
  기준선    같은 기획서 ID를 참조하는 작업을 모두 영향으로 보는 규칙

지표
  정밀도 = 맞힌 후보 / 전체 후보   (알림이 헛나가는 정도)
  재현율 = 맞힌 후보 / 정답 전체   (놓친 정도)

실행: python src/evaluate.py  ->  화면 출력 + results/summary.md
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from impact_analyzer import ROOT, load_offline  # noqa: E402


def score(pred: set, truth: set):
    hit = pred & truth
    precision = len(hit) / len(pred) if pred else 0.0
    recall = len(hit) / len(truth) if truth else 0.0
    return hit, precision, recall


def main():
    tasks, changes = load_offline()
    lines = ["| 변경 | 방식 | 후보 | 맞힘 | 정답 | 정밀도 | 재현율 | 놓친 작업 | 헛짚은 작업 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    totals = {"기준선": [0, 0, 0], "AI": [0, 0, 0]}  # 후보, 맞힘, 정답

    for cid in sorted(changes):
        c = changes[cid]
        truth = set(c["truth"])
        methods = {"기준선": {t["key"] for t in tasks if t["spec_id"] == c["spec_id"]}}
        result_file = ROOT / "results" / f"{cid}.json"
        if result_file.exists():
            data = json.loads(result_file.read_text(encoding="utf-8"))
            methods["AI"] = {i["key"] for i in data["impacted"]}
        for name, pred in methods.items():
            hit, p, r = score(pred, truth)
            totals[name][0] += len(pred)
            totals[name][1] += len(hit)
            totals[name][2] += len(truth)
            missed = ", ".join(sorted(truth - pred)) or "-"
            wrong = ", ".join(sorted(pred - truth)) or "-"
            lines.append(f"| {cid} | {name} | {len(pred)} | {len(hit)} | {len(truth)} | "
                         f"{p:.0%} | {r:.0%} | {missed} | {wrong} |")

    for name, (n_pred, n_hit, n_truth) in totals.items():
        if n_pred:
            lines.append(f"| 합계 | {name} | {n_pred} | {n_hit} | {n_truth} | "
                         f"{n_hit / n_pred:.0%} | {n_hit / n_truth:.0%} | | |")

    table = "\n".join(lines)
    print(table)
    out = ROOT / "results" / "summary.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(table + "\n", encoding="utf-8")
    if not any((ROOT / "results").glob("C*.json")):
        print("\nAI 결과 파일이 없어 기준선만 계산했습니다. "
              "python src/impact_analyzer.py offline --change all 을 먼저 실행하세요.")


if __name__ == "__main__":
    main()
