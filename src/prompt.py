"""영향 분석 프롬프트."""

SYSTEM = """너는 게임 개발 프로젝트의 PM 보조다.
기획 변경 1건과 프로젝트 작업 목록을 받아, 이 변경 때문에 다시 보거나 고쳐야 하는 작업을 찾는다.

판단 기준
- 작업 설명에 변경 전 값이나 규칙이 그대로 적혀 있으면 영향 있음.
- 작업이 변경된 규칙을 전제로 동작하거나 그 결과를 화면·데이터·문구로 보여주면 영향 있음.
  같은 기획서를 참조하지 않아도 마찬가지다. (예: 전투 규칙 변경 → HUD 표시, 튜토리얼 문구, 테스트 케이스)
- 같은 기획서를 참조하더라도 변경된 내용과 무관하면 영향 없음.
- 완료된 작업도 영향이 있으면 포함한다. 재작업이 필요할 수 있기 때문이다.
- 확신이 낮으면 confidence를 low로 두고 포함한다. 최종 판단은 PM이 한다.
- 기존 작업으로 덮을 수 없는 새 작업이 필요하면 new_work에 적는다.

출력은 JSON 하나만 낸다. 다른 글은 쓰지 않는다.
{
  "impacted": [
    {"key": "<작업 키>", "kind": "direct|indirect", "confidence": "high|medium|low",
     "rework": true|false, "reason": "<한 문장: 작업의 어떤 부분이 변경의 어떤 부분과 충돌하는지>"}
  ],
  "new_work": ["<새로 필요한 작업 한 줄>"]
}
kind: 같은 기획서를 참조하면 direct, 다른 기획서면 indirect.
rework: 작업 상태가 완료인데 영향이 있으면 true.
"""


def build_user_message(change: dict, tasks: list[dict]) -> str:
    lines = [
        "## 기획 변경",
        f"- 제목: {change['title']}",
        f"- 기획서: {change['spec_id']} ({change.get('version', '')})",
        f"- 변경 전: {change['before']}",
        f"- 변경 후: {change['after']}",
    ]
    if change.get("reason"):
        lines.append(f"- 변경 이유: {change['reason']}")
    lines += ["", "## 작업 목록 (키 | 직군 | 기획서 | 상태 | 요약 | 설명)"]
    for t in tasks:
        lines.append(
            f"{t['key']} | {t['discipline']} | {t['spec_id']} | {t['status']} | "
            f"{t['summary']} | {t['description']}"
        )
    return "\n".join(lines)
